package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/sadiq/human-archive/pipeline/internal/classifier"
	"github.com/sadiq/human-archive/pipeline/internal/downloader"
	"github.com/sadiq/human-archive/pipeline/internal/s3client"
)

func main() {
	bucket := flag.String("bucket", "demo-hand-tracking-bucket", "source S3 bucket")
	prefix := flag.String("prefix", "", "S3 key prefix filter")
	workers := flag.Int("workers", 2, "concurrent download workers")
	bufSize := flag.Int("buffer", 4, "output channel buffer size")
	tempDir := flag.String("temp-dir", os.TempDir(), "directory for downloaded video files")
	limit := flag.Int("limit", 2, "max number of videos to download (default 2)")
	profile := flag.String("profile", "humanarchive", "AWS profile name")
	configDir := flag.String("aws-config-dir", "~/.aws.humanarchive", "AWS config directory")

	outputRoot := flag.String("output", "../output", "root dir where per-video results are written")
	classifierDir := flag.String("classifier-dir", "../classifier", "path to Python classifier project (uv root)")
	keepVideos := flag.Bool("keep-videos", false, "keep downloaded videos after classification")

	baseFPS := flag.Float64("base-fps", 1.0, "uniform sampling rate")
	eventFPS := flag.Float64("event-fps", 5.0, "fps within event context windows")
	denseFPS := flag.Float64("dense-fps", 10.0, "fps at sharp transitions")
	contextS := flag.Float64("context-s", 3.0, "+- seconds around each event")
	scanFPS := flag.Float64("scan-fps", 1.0, "pass-1 coarse scan rate")
	maxFrames := flag.Int("max-frames-per-video", 0, "cap on sampled frames per video (0 = no cap)")
	primaryConf := flag.Float64("primary-conf", 0.5, "strict detector min confidence")
	secondaryConf := flag.Float64("secondary-conf", 0.15, "permissive detector min confidence")
	presenceThresh := flag.Float64("presence-threshold", 0.06, "pass-1 skin mask threshold")
	noVideoMode := flag.Bool("no-video-mode", false, "disable MediaPipe VIDEO running mode")

	flag.Parse()

	log.Printf("pipeline start  bucket=%s prefix=%q limit=%d output=%s", *bucket, *prefix, *limit, *outputRoot)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	if err := os.MkdirAll(*outputRoot, 0o755); err != nil {
		log.Fatalf("mkdir output: %v", err)
	}

	s3c, err := s3client.New(ctx, *profile, *configDir)
	if err != nil {
		log.Fatalf("init s3 client: %v", err)
	}

	pipe := downloader.New(s3c, downloader.Config{
		Bucket:     *bucket,
		Prefix:     *prefix,
		Workers:    *workers,
		BufferSize: *bufSize,
		TempDir:    *tempDir,
		Limit:      *limit,
	})

	absClassifier, err := filepath.Abs(*classifierDir)
	if err != nil {
		log.Fatalf("resolve classifier dir: %v", err)
	}
	absOutput, err := filepath.Abs(*outputRoot)
	if err != nil {
		log.Fatalf("resolve output dir: %v", err)
	}

	runner := &classifier.Runner{
		ProjectDir:     absClassifier,
		BaseFPS:        *baseFPS,
		EventFPS:       *eventFPS,
		DenseFPS:       *denseFPS,
		ContextS:       *contextS,
		ScanFPS:        *scanFPS,
		MaxFrames:      *maxFrames,
		PrimaryConf:    *primaryConf,
		SecondaryConf:  *secondaryConf,
		PresenceThresh: *presenceThresh,
		NoVideoMode:    *noVideoMode,
	}

	videos, errc := pipe.Run(ctx)

	processed := 0
	for item := range videos {
		processed++
		fmt.Printf("[%d] downloaded  key=%s  path=%s  size=%.1fMB\n",
			processed, item.Key, item.LocalPath, float64(item.Size)/1e6)

		sum, cerr := runner.Classify(ctx, item.LocalPath, item.Key, absOutput)
		if cerr != nil {
			log.Printf("classifier failed for %s: %v", item.Key, cerr)
		} else {
			fmt.Printf("     classified  frames=%d  labels=%v  reasons=%v  dir=%s\n",
				sum.SampledFrames, sum.LabelCounts, sum.ReasonCounts, sum.OutputDir)
		}

		if !*keepVideos {
			if err := os.Remove(item.LocalPath); err != nil {
				log.Printf("cleanup %s: %v", item.LocalPath, err)
			}
		}
	}

	if err := <-errc; err != nil {
		log.Fatalf("pipeline error: %v", err)
	}

	log.Printf("pipeline done: %d videos processed", processed)
}
