// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package git

import (
	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/config"
)

type RemoteInfo struct {
	Name string
	URL  string
}

func (s *Service) AddRemote(name, url string, fetch, overwrite bool) error {
	repo, err := git.PlainOpen(s.WorkDir)
	if err != nil {
		return err
	}

	if overwrite {
		if _, err := repo.Remote(name); err == nil {
			if err := repo.DeleteRemote(name); err != nil {
				return err
			}
		}
	}

	remoteConfig := &config.RemoteConfig{
		Name: name,
		URLs: []string{url},
	}
	if err := remoteConfig.Validate(); err != nil {
		return err
	}

	remote, err := repo.CreateRemote(remoteConfig)
	if err != nil {
		return err
	}

	if fetch {
		if err := remote.Fetch(&git.FetchOptions{}); err != nil && err != git.NoErrAlreadyUpToDate {
			return err
		}
	}

	return nil
}

func (s *Service) ListRemotes() ([]RemoteInfo, error) {
	repo, err := git.PlainOpen(s.WorkDir)
	if err != nil {
		return nil, err
	}

	remotes, err := repo.Remotes()
	if err != nil {
		return nil, err
	}

	result := make([]RemoteInfo, 0, len(remotes))
	for _, remote := range remotes {
		cfg := remote.Config()
		url := ""
		if len(cfg.URLs) > 0 {
			url = cfg.URLs[0]
		}
		result = append(result, RemoteInfo{Name: cfg.Name, URL: url})
	}

	return result, nil
}
