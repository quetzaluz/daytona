// Copyright 2025 Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package git

import (
	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
)

func (s *Service) CreateBranch(name string) error {
	repo, err := git.PlainOpen(s.WorkDir)
	if err != nil {
		return err
	}

	w, err := repo.Worktree()
	if err != nil {
		return err
	}

	return w.Checkout(&git.CheckoutOptions{
		Create: true,
		Branch: plumbing.NewBranchReferenceName(name),
	})
}

func (s *Service) ListBranches() ([]string, string, error) {
	repo, err := git.PlainOpen(s.WorkDir)
	if err != nil {
		return []string{}, "", err
	}

	branches, err := repo.Branches()
	if err != nil {
		return []string{}, "", err
	}

	var branchList []string
	err = branches.ForEach(func(ref *plumbing.Reference) error {
		branchList = append(branchList, ref.Name().Short())
		return nil
	})
	if err != nil {
		return branchList, "", err
	}

	current := ""
	if head, headErr := repo.Head(); headErr == nil && head.Name().IsBranch() {
		current = head.Name().Short()
	}

	return branchList, current, nil
}

func (s *Service) DeleteBranch(name string) error {
	repo, err := git.PlainOpen(s.WorkDir)
	if err != nil {
		return err
	}
	return repo.Storer.RemoveReference(plumbing.NewBranchReferenceName(name))
}
