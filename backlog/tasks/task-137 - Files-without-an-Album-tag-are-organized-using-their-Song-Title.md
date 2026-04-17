---
id: TASK-137
title: Files without an Album tag are organized using their Song Title
status: To Do
assignee: []
created_date: '2026-04-17 20:23'
labels: []
milestone: m-27
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
If there is no Album tag in a file, we currently organize it as though there is no artist or album information.

With the referenced file, there are two issues:
* The Artist is present in the file tags ("Mndsgn.") - It looks like Album Artist isn't present, so we have no fallback.
* The Album isn't present in file tags, but the file is its own standalone release and shouldn't be grouped with other files that don't have album tags

In the Library view, we should show this file as its own "album" with the song title in *Italics* to indicate the metadata issue.
<!-- SECTION:DESCRIPTION:END -->
