package uploader

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func UploadFrames(ctx context.Context, client *s3.Client, bucket, outputDir string) (int, error) {
	framesDir := filepath.Join(outputDir, "frames")
	info, err := os.Stat(framesDir)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, fmt.Errorf("stat frames dir %s: %w", framesDir, err)
	}
	if !info.IsDir() {
		return 0, nil
	}

	stem := filepath.Base(outputDir)

	uploaded := 0
	err = filepath.Walk(framesDir, func(path string, fi os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if fi.IsDir() {
			return nil
		}
		if !strings.HasSuffix(strings.ToLower(fi.Name()), ".jpg") {
			return nil
		}

		rel, err := filepath.Rel(framesDir, path)
		if err != nil {
			return err
		}

		key := fmt.Sprintf("%s/%s", stem, rel)

		f, err := os.Open(path)
		if err != nil {
			return fmt.Errorf("open %s: %w", path, err)
		}

		_, putErr := client.PutObject(ctx, &s3.PutObjectInput{
			Bucket:      aws.String(bucket),
			Key:         aws.String(key),
			Body:        f,
			ContentType: aws.String("image/jpeg"),
		})
		f.Close()
		if putErr != nil {
			return fmt.Errorf("s3 upload %s: %w", key, putErr)
		}

		uploaded++
		if uploaded%100 == 0 {
			log.Printf("uploaded %d frames to s3://%s/%s...", uploaded, bucket, key)
		}
		return nil
	})
	if err != nil {
		return uploaded, err
	}

	log.Printf("uploaded %d frames to s3://%s/%s", uploaded, bucket, stem)
	return uploaded, nil
}
