// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package git

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestUsesLegacyGitFields(t *testing.T) {
	cases := []struct {
		name   string
		header map[string]string
		legacy bool
	}{
		{"no headers -> new behavior", nil, false},
		{"old go sdk -> legacy", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "0.185.0"}, true},
		{"much older go sdk -> legacy", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "0.140.2"}, true},
		{"go sdk exact min version -> new behavior", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": gitFieldsMinSDKVersion}, false},
		{"go sdk newer version -> new behavior", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "0.999.0"}, false},
		{"go sdk unparseable version -> new behavior", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "garbage"}, false},
		{"go sdk v0.0.0-dev build -> new behavior", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "v0.0.0-dev"}, false},
		{"go sdk 0.0.0 -> new behavior", map[string]string{"X-Daytona-Source": "sdk-go", "X-Daytona-SDK-Version": "0.0.0"}, false},
		{"old version but no source -> new behavior", map[string]string{"X-Daytona-SDK-Version": "0.185.0"}, false},
		{"old NON-go sdk -> new behavior", map[string]string{"X-Daytona-Source": "sdk-python", "X-Daytona-SDK-Version": "0.185.0"}, false},
		{"go sdk via websocket subprotocol (old) -> legacy", map[string]string{"X-Daytona-Source": "sdk-go", "Sec-WebSocket-Protocol": "X-Daytona-SDK-Version~0.185.0"}, true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := http.Header{}
			for k, v := range tc.header {
				h.Set(k, v)
			}
			require.Equal(t, tc.legacy, usesLegacyGitFields(h))
		})
	}
}

func TestGetStatus_LegacyClientOmitsNewFields(t *testing.T) {
	gin.SetMode(gin.TestMode)
	repo := initRepoWithUpstream(t)

	// New client (no version header): detached omitted (false+omitempty) but
	// upstream present.
	newResp := doGitGet(t, GetStatus, "/git/status?path="+repo, "")
	require.Equal(t, "origin/master", newResp["upstream"], "new client must receive upstream")

	// Legacy client: upstream stripped, exact old wire shape.
	oldResp := doGitGet(t, GetStatus, "/git/status?path="+repo, "0.185.0")
	_, hasUpstream := oldResp["upstream"]
	require.False(t, hasUpstream, "legacy client must not receive upstream")
	_, hasDetached := oldResp["detached"]
	require.False(t, hasDetached, "legacy client must not receive detached")
	require.Equal(t, "master", oldResp["currentBranch"], "old fields must remain")
}

func TestListBranches_LegacyClientOmitsCurrent(t *testing.T) {
	gin.SetMode(gin.TestMode)
	repo := initRepoWithUpstream(t)

	newResp := doGitGet(t, ListBranches, "/git/branches?path="+repo, "")
	require.Equal(t, "master", newResp["current"], "new client must receive current")

	oldResp := doGitGet(t, ListBranches, "/git/branches?path="+repo, "0.185.0")
	_, hasCurrent := oldResp["current"]
	require.False(t, hasCurrent, "legacy client must not receive current")
	require.NotNil(t, oldResp["branches"], "branches must remain")
}

func doGitGet(t *testing.T, handler gin.HandlerFunc, target, sdkVersion string) map[string]any {
	t.Helper()
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest(http.MethodGet, target, nil)
	if sdkVersion != "" {
		// Simulate an old Go SDK: both the source and version headers are required
		// for the legacy gate to engage.
		c.Request.Header.Set("X-Daytona-Source", "sdk-go")
		c.Request.Header.Set("X-Daytona-SDK-Version", sdkVersion)
	}
	handler(c)
	require.Equal(t, http.StatusOK, w.Code, "body: %s", w.Body.String())

	var resp map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	return resp
}

// initRepoWithUpstream creates a repo on branch "master" with an upstream
// tracking ref (origin/master) configured locally, so GetGitStatus reports a
// non-empty Upstream without needing network access.
func initRepoWithUpstream(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	run := func(args ...string) {
		cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
		cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null")
		out, err := cmd.CombinedOutput()
		require.NoError(t, err, "git %v: %s", args, out)
	}

	run("init", "-b", "master")
	run("config", "user.email", "test@example.com")
	run("config", "user.name", "Test")
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.txt"), []byte("hi\n"), 0o644))
	run("add", "a.txt")
	run("commit", "-m", "init")
	// Configure a local upstream without a real remote fetch.
	run("remote", "add", "origin", dir)
	run("update-ref", "refs/remotes/origin/master", "HEAD")
	run("config", "branch.master.remote", "origin")
	run("config", "branch.master.merge", "refs/heads/master")

	return dir
}
