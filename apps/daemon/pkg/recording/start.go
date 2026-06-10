// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package recording

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/daytonaio/daemon/pkg/childreap"
	"github.com/google/uuid"
)

// StartRecording starts a new screen recording session. The flow is shared
// across platforms; only the ffmpeg capture command differs (see the
// per-platform newCaptureCmd).
func (s *RecordingService) StartRecording(label *string) (*Recording, error) {
	// Ensure recordings directory exists
	if err := os.MkdirAll(s.recordingsDir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create recordings directory: %w", err)
	}

	// Check if ffmpeg is available
	ffmpegPath, err := exec.LookPath("ffmpeg")
	if err != nil {
		return nil, ErrFFmpegNotFound
	}

	// Generate recording ID and filename
	// ID is included in filename so it can be recovered when scanning disk
	id := uuid.New().String()
	now := time.Now()
	timestamp := now.Format("20060102_150405")

	// Validate label if provided (reject invalid labels without modification)
	if label != nil && *label != "" {
		if err := validateLabel(*label); err != nil {
			return nil, err
		}
	}

	var fileName string
	if label != nil && *label != "" {
		fileName = fmt.Sprintf("%s_%s_%s.mp4", id, *label, timestamp)
	} else {
		fileName = fmt.Sprintf("%s_session_%s.mp4", id, timestamp)
	}

	filePath := filepath.Join(s.recordingsDir, fileName)

	// Create recording entry
	recording := &Recording{
		ID:        id,
		FileName:  fileName,
		FilePath:  filePath,
		StartTime: now,
		Status:    "recording",
	}

	cmd := newCaptureCmd(ffmpegPath, filePath)

	// Get stdin pipe for graceful shutdown
	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("failed to get stdin pipe: %w", err)
	}

	// Start ffmpeg process
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("failed to start ffmpeg: %w", err)
	}

	s.logger.Debug("Started recording", "id", id, "path", filePath)

	// Create a done channel to receive the wait result exactly once
	done := make(chan error, 1)

	// Store active recording
	s.activeRecordings.Set(id, &activeRecording{
		recording: recording,
		cmd:       cmd,
		stdinPipe: stdinPipe,
		done:      done,
	})

	// Start a goroutine to wait for the process and handle unexpected exits.
	// Reap (not Wait): we don't read stdout/stderr; ffmpeg output goes to a
	// file via -y target, so no Go I/O goroutines to drain.
	go func() {
		exitCode, err := childreap.Reap(cmd)
		done <- err // Signal the done channel with the result

		// Atomically remove from active recordings if still there
		if active, exists := s.activeRecordings.Pop(id); exists {
			// childreap.Reap returns (exitCode, nil) for *exec.ExitError
			// cases — unlike a raw cmd.Wait, where non-zero exits surface
			// as err != nil. We have to check both here so a crashed/killed
			// ffmpeg (OOM, SIGKILL, corrupt input, etc.) still gets marked
			// failed instead of silently retaining whatever status it had.
			if err != nil || exitCode != 0 {
				s.logger.Warn("Recording ffmpeg process exited unexpectedly",
					"id", id, "exitCode", exitCode, "error", err)
				active.recording.Status = "failed"
			}
		}
	}()

	return recording, nil
}
