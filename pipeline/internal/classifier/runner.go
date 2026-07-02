package classifier

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

// Runner invokes the Python classifier as a subprocess for a single video.
type Runner struct {
	ProjectDir     string
	BaseFPS        float64
	EventFPS       float64
	DenseFPS       float64
	ContextS       float64
	ScanFPS        float64
	MaxFrames      int
	PrimaryConf    float64
	SecondaryConf  float64
	NoVideoMode    bool
	PresenceThresh float64
}

type Summary struct {
	Video         string         `json:"video"`
	OutputDir     string         `json:"output_dir"`
	SampledFrames int            `json:"sampled_frames"`
	LabelCounts   map[string]int `json:"label_counts"`
	ReasonCounts  map[string]int `json:"reason_counts"`
	JSONReport    string         `json:"json_report"`
}

func stemFromKey(key string) string {
	trimmed := strings.TrimSuffix(key, filepath.Ext(key))
	trimmed = strings.Trim(trimmed, "/")
	if trimmed == "" {
		return ""
	}
	// full path so multiple videos with the same basename ("clip.mp4") don't collide
	safe := strings.ReplaceAll(trimmed, "/", "__")
	return safe
}

func f(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

// Classify runs the classifier on videoPath and writes categorised frames
// under outputRoot/<stem>/frames/<label>/*.jpg plus outputRoot/<stem>/report.json.
func (r *Runner) Classify(ctx context.Context, videoPath, sourceKey, outputRoot string) (*Summary, error) {
	stem := stemFromKey(sourceKey)
	if stem == "" {
		stem = stemFromKey(videoPath)
	}
	videoOut := filepath.Join(outputRoot, stem)
	framesDir := filepath.Join(videoOut, "frames")
	if err := os.MkdirAll(framesDir, 0o755); err != nil {
		return nil, fmt.Errorf("mkdir %s: %w", framesDir, err)
	}
	report := filepath.Join(videoOut, "report.json")

	args := []string{
		"run", "classify-video",
		videoPath,
		"--out", report,
		"--frames-dir", framesDir,
		"--base-fps", f(r.BaseFPS),
		"--event-fps", f(r.EventFPS),
		"--dense-fps", f(r.DenseFPS),
		"--context-s", f(r.ContextS),
		"--scan-fps", f(r.ScanFPS),
		"--primary-conf", f(r.PrimaryConf),
		"--secondary-conf", f(r.SecondaryConf),
		"--presence-threshold", f(r.PresenceThresh),
	}
	if r.MaxFrames > 0 {
		args = append(args, "--max-frames", strconv.Itoa(r.MaxFrames))
	}
	if r.NoVideoMode {
		args = append(args, "--no-video-mode")
	}

	cmd := exec.CommandContext(ctx, "uv", args...)
	cmd.Dir = r.ProjectDir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	log.Printf("classify %s -> %s", videoPath, videoOut)
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("classify %s: %w", videoPath, err)
	}

	sum, err := readSummary(report)
	if err != nil {
		return nil, err
	}
	sum.Video = videoPath
	sum.OutputDir = videoOut
	sum.JSONReport = report
	return sum, nil
}

func readSummary(reportPath string) (*Summary, error) {
	b, err := os.ReadFile(reportPath)
	if err != nil {
		return nil, fmt.Errorf("read report %s: %w", reportPath, err)
	}
	var payload struct {
		Stats struct {
			SampledFrames int            `json:"sampled_frames"`
			LabelCounts   map[string]int `json:"label_counts"`
			ReasonCounts  map[string]int `json:"reason_counts"`
		} `json:"stats"`
	}
	if err := json.Unmarshal(b, &payload); err != nil {
		return nil, fmt.Errorf("parse report %s: %w", reportPath, err)
	}
	return &Summary{
		SampledFrames: payload.Stats.SampledFrames,
		LabelCounts:   payload.Stats.LabelCounts,
		ReasonCounts:  payload.Stats.ReasonCounts,
	}, nil
}
