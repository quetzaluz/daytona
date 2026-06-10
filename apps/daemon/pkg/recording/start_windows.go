//go:build windows

// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package recording

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// newCaptureCmd builds the ffmpeg command for Windows screen capture.
// -f gdigrab: GDI screen capture
// -framerate 30: 30 FPS
// -i desktop: capture the entire virtual screen of the calling session
// -c:v libx264: H.264 codec
// -preset ultrafast: fast encoding for real-time capture
// -pix_fmt yuv420p: standard pixel format for compatibility
//
// KNOWN LIMITATION: ffmpeg inherits the daemon's session/window station, so
// when the daemon runs as SYSTEM in session 0, gdigrab captures the invisible
// session-0 desktop, not the interactive user's. Spawning with the console
// user's token (see computeruse/manager.activeConsoleUserToken) is a planned
// cross-package follow-up.
func newCaptureCmd(ffmpegPath, filePath string) *exec.Cmd {
	cmd := exec.Command(ffmpegPath,
		"-f", "gdigrab",
		"-framerate", "30",
		"-i", "desktop",
		"-c:v", "libx264",
		"-preset", "ultrafast",
		"-pix_fmt", "yuv420p",
		"-y", // Overwrite output file if exists
		filePath,
	)

	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NO_WINDOW,
	}

	return cmd
}
