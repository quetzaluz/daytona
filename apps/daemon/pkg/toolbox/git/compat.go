// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package git

import (
	"net/http"

	semver "github.com/Masterminds/semver/v3"
	"github.com/daytonaio/daemon/internal/util"
)

const (
	// gitFieldsMinSDKVersion is the first Go SDK release whose generated client
	// tolerates unknown response fields. Older Go SDKs decode responses with a
	// strict JSON decoder (DisallowUnknownFields) and error on the newer git
	// fields (detached/upstream on status, current on branches), so the status
	// and branches handlers omit those fields for them.
	//
	// IMPORTANT: keep this in sync with the Go SDK release that ships these fields.
	gitFieldsMinSDKVersion = "0.186.0"

	// goSDKSource is the X-Daytona-Source value set by the Go SDK.
	goSDKSource = "sdk-go"
)

// usesLegacyGitFields reports whether the request comes from a client that breaks
// on the newer git response fields. Only the Go SDK older than
// gitFieldsMinSDKVersion is affected — its generated client uses a strict JSON
// decoder that rejects unknown fields. Every other SDK (and the Go SDK from
// gitFieldsMinSDKVersion onward) tolerates unknown fields, so they always receive
// the new fields. An absent/unparseable version, a 0.0.0 dev build, or a non-Go
// source defaults to the new behavior.
func usesLegacyGitFields(header http.Header) bool {
	if header.Get("X-Daytona-Source") != goSDKSource {
		return false
	}
	v := util.ExtractSdkVersionFromHeader(header)
	if v == "" {
		return false
	}
	// Dev/placeholder builds (v0.0.0-dev) are built from current source and
	// tolerate the new fields, so treat them as up to date rather than legacy.
	if sv, err := semver.NewVersion(v); err == nil && sv.Major() == 0 && sv.Minor() == 0 && sv.Patch() == 0 {
		return false
	}
	cmp, err := util.CompareVersions(v, gitFieldsMinSDKVersion)
	if err != nil {
		return false
	}
	return *cmp < 0
}
