package s3client

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

const (
	DefaultProfile   = "humanarchive"
	DefaultConfigDir = "~/.aws.humanarchive"
)

func expandHome(path string) string {
	if len(path) >= 2 && path[:2] == "~/" {
		home, _ := os.UserHomeDir()
		return filepath.Join(home, path[2:])
	}
	return path
}

func parseINI(path, section string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	values := map[string]string{}
	inSection := false
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			name := strings.Trim(line, "[]")
			inSection = name == section || name == "profile "+section
			continue
		}
		if inSection {
			if k, v, ok := strings.Cut(line, "="); ok {
				values[strings.TrimSpace(k)] = strings.TrimSpace(v)
			}
		}
	}
	return values, scanner.Err()
}

func New(_ context.Context, profile, configDir string) (*s3.Client, error) {
	dir := expandHome(configDir)
	configFile := filepath.Join(dir, "config")
	credsFile := filepath.Join(dir, "credentials")

	creds, err := parseINI(credsFile, profile)
	if err != nil {
		return nil, fmt.Errorf("parse credentials %s: %w", credsFile, err)
	}
	keyID := creds["aws_access_key_id"]
	secret := creds["aws_secret_access_key"]
	if keyID == "" || secret == "" {
		return nil, fmt.Errorf("credentials not found for profile %q in %s", profile, credsFile)
	}

	cfgVals, err := parseINI(configFile, profile)
	if err != nil {
		return nil, fmt.Errorf("parse config %s: %w", configFile, err)
	}
	region := cfgVals["region"]
	if region == "" {
		region = "ap-south-1"
	}

	cfg := aws.Config{
		Region:      region,
		Credentials: credentials.NewStaticCredentialsProvider(keyID, secret, ""),
	}

	return s3.NewFromConfig(cfg), nil
}
