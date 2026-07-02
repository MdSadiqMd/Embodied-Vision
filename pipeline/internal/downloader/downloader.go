package downloader

import (
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type VideoItem struct {
	Key       string
	Bucket    string
	LocalPath string
	Size      int64
	ETag      string
}

type Config struct {
	Bucket     string
	Prefix     string
	Workers    int
	BufferSize int
	TempDir    string
	Limit      int
}

type Pipeline struct {
	client *s3.Client
	cfg    Config
}

func New(client *s3.Client, cfg Config) *Pipeline {
	if cfg.Workers <= 0 {
		cfg.Workers = 2
	}
	if cfg.BufferSize <= 0 {
		cfg.BufferSize = 4
	}
	if cfg.TempDir == "" {
		cfg.TempDir = os.TempDir()
	}
	if cfg.Limit <= 0 {
		cfg.Limit = 2
	}
	return &Pipeline{client: client, cfg: cfg}
}

// Run starts the download pipeline and returns a channel of ready VideoItems.
// The channel is closed when all downloads complete or ctx is cancelled.
func (p *Pipeline) Run(ctx context.Context) (<-chan VideoItem, <-chan error) {
	out := make(chan VideoItem, p.cfg.BufferSize)
	errc := make(chan error, 1)

	go func() {
		defer close(out)
		defer close(errc)

		log.Printf("listing .mp4 keys from s3://%s/%s ...", p.cfg.Bucket, p.cfg.Prefix)
		keys, listErr := p.listMP4Keys(ctx)
		if listErr != nil {
			errc <- fmt.Errorf("list s3 keys: %w", listErr)
			return
		}

		keysCh := make(chan string, p.cfg.Workers)
		var wg sync.WaitGroup

		for i := range p.cfg.Workers {
			wg.Add(1)
			go func(workerID int) {
				defer wg.Done()
				for key := range keysCh {
					item, err := p.download(ctx, key)
					if err != nil {
						log.Printf("[worker %d] skip %s: %v", workerID, key, err)
						continue
					}
					select {
					case out <- item:
					case <-ctx.Done():
						os.Remove(item.LocalPath)
						return
					}
				}
			}(i)
		}

	feed:
		for _, key := range keys {
			select {
			case keysCh <- key:
			case <-ctx.Done():
				break feed
			}
		}
		close(keysCh)
		wg.Wait()
	}()

	return out, errc
}

func (p *Pipeline) listMP4Keys(ctx context.Context) ([]string, error) {
	var keys []string
	pageNum := 0
	paginator := s3.NewListObjectsV2Paginator(p.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(p.cfg.Bucket),
		Prefix: aws.String(p.cfg.Prefix),
	})

	for paginator.HasMorePages() {
		pageNum++
		log.Printf("fetching list page %d ...", pageNum)
		pg, err := paginator.NextPage(ctx)
		if err != nil {
			return nil, err
		}
		log.Printf("page %d: %d objects", pageNum, len(pg.Contents))
		for _, obj := range pg.Contents {
			key := aws.ToString(obj.Key)
			if !strings.HasSuffix(strings.ToLower(key), ".mp4") {
				continue
			}
			keys = append(keys, key)
			if p.cfg.Limit > 0 && len(keys) >= p.cfg.Limit {
				log.Printf("limit %d reached, stopping list", p.cfg.Limit)
				return keys, nil
			}
		}
	}

	log.Printf("found %d .mp4 files in s3://%s/%s", len(keys), p.cfg.Bucket, p.cfg.Prefix)
	return keys, nil
}

func (p *Pipeline) download(ctx context.Context, key string) (VideoItem, error) {
	resp, err := p.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(p.cfg.Bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return VideoItem{}, fmt.Errorf("get object %s: %w", key, err)
	}
	defer resp.Body.Close()

	name := filepath.Base(key)
	tmp, err := os.CreateTemp(p.cfg.TempDir, "video-*-"+name)
	if err != nil {
		return VideoItem{}, fmt.Errorf("create temp file: %w", err)
	}

	n, err := copyWithProgress(ctx, tmp, resp.Body, key)
	tmp.Close()
	if err != nil {
		os.Remove(tmp.Name())
		return VideoItem{}, fmt.Errorf("write %s: %w", key, err)
	}

	log.Printf("downloaded %s -> %s (%.1f MB)", key, tmp.Name(), float64(n)/1e6)

	return VideoItem{
		Key:       key,
		Bucket:    p.cfg.Bucket,
		LocalPath: tmp.Name(),
		Size:      n,
		ETag:      aws.ToString(resp.ETag),
	}, nil
}

const progressChunk = 25 * 1024 * 1024 // log every 25 MB

func copyWithProgress(ctx context.Context, dst *os.File, src io.Reader, key string) (int64, error) {
	buf := make([]byte, 32*1024)
	var total int64
	var lastLogged int64

	for {
		if ctx.Err() != nil {
			return total, ctx.Err()
		}
		nr, rerr := src.Read(buf)
		if nr > 0 {
			nw, werr := dst.Write(buf[:nr])
			total += int64(nw)
			if werr != nil {
				return total, werr
			}
			if total-lastLogged >= progressChunk {
				log.Printf("  %s  %.0f MB received", key, float64(total)/1e6)
				lastLogged = total
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			return total, rerr
		}
	}
	return total, nil
}
