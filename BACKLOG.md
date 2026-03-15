# Backlog
## Bug: File Operations on an already moved file

It looks like occasionally tag writing or analysis is attempted after a file has already been moved. This should never happen: file operations after tagging should only happen after all tagging operations are completed.

This is an occasional bug that results in an uncaught exception that halts the daemon:

```
2026-03-15 14:02:34  ERROR     tune_shifter.watcher  Unhandled error in pipeline for /Users/theodore.terry/Downloads/_music_staging/Earthless - Black Heaven
Traceback (most recent call last):
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_util.py", line 251, in _openfile
    fileobj = open(filename, "rb+" if writable else "rb")
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '/Users/theodore.terry/Downloads/_music_staging/Earthless - Black Heaven/Earthless - Black Heaven - 01 Gifted by the Wind.mp3'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/Users/theodore.terry/repos/tune-shifter/tune_shifter/watcher.py", line 111, in _process
    run(path, self._config)
  File "/Users/theodore.terry/repos/tune-shifter/tune_shifter/pipeline.py", line 42, in run
    release = tag_directory(directory, audio_files)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/theodore.terry/repos/tune-shifter/tune_shifter/tagger.py", line 96, in tag_directory
    _write_tags(audio_file, release)
  File "/Users/theodore.terry/repos/tune-shifter/tune_shifter/tagger.py", line 298, in _write_tags
    _write_mp3_tags(path, release, track_info)
  File "/Users/theodore.terry/repos/tune-shifter/tune_shifter/tagger.py", line 340, in _write_mp3_tags
    tags = id3.ID3(str(path))
           ^^^^^^^^^^^^^^^^^^
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/id3/_file.py", line 76, in __init__
    super(ID3, self).__init__(*args, **kwargs)
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/id3/_tags.py", line 175, in __init__
    super(ID3Tags, self).__init__(*args, **kwargs)
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_util.py", line 534, in __init__
    super(DictProxy, self).__init__(*args, **kwargs)
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_tags.py", line 110, in __init__
    self.load(*args, **kwargs)
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_util.py", line 185, in wrapper
    return func(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_util.py", line 154, in wrapper
    with _openfile(self, filething, filename, fileobj,
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/contextlib.py", line 137, in __enter__
    return next(self.gen)
           ^^^^^^^^^^^^^^
  File "/Users/theodore.terry/.pyenv/versions/3.12.10/lib/python3.12/site-packages/mutagen/_util.py", line 272, in _openfile
    raise MutagenError(e)
mutagen.MutagenError: [Errno 2] No such file or directory: '/Users/theodore.terry/Downloads/_music_staging/Earthless - Black Heaven/Earthless - Black Heaven - 01 Gifted by the Wind.mp3'
```

## Bug: Uncaught Attribute Type-Id

Is this a problem?  If so, fix it. If not, put in debug level logging if possible (since it's from a different library).

```
26-03-15 12:14:58  INFO      tune_shifter.tagger  Searching MusicBrainz for artist='Earthless' album='Black Heaven'
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:58  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:release-group>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:label>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:artist>, uncaught attribute type-id
2026-03-15 12:14:59  INFO      musicbrainzngs  in <ws2:recording>, uncaught <first-release-date>
2026-03-15 12:14:59  INFO      tune_shifter.tagger  Matched release: 'Black Heaven' (mbid=b0562a95-2dbf-419a-85d9-eca1d082f682)
```

## Optimization: Check existing tags / embedded image to see if they even need to be updated

## Best Release

*When there are multiple releases available, I want the tags for the release closest to the original physical format*

## AcoustID Support

> requires audio fingerprinting (fpcalc/chromaprint); can't be fetched from MusicBrainz

## Producer Support

> requires a recording-rels include and relationship traversal; deferred

## Config Arguments

*Don't make me ever edit the config file: let me set config through an argument*

Running `tune-shifter config set paths.staging` lets a user set the staging path, etc.

Running `tune-shifter config show` shows the whole config.

## Code Coverage

CI runs a code coverage tool.

The application has 95% code coverage in its testing.

## Switch to Poetry for dependency management

## FLAC Support

*I want FLAC to be as well supported as MP3 and M4A for tagging*

## OGG Support

*I want OGG to be as well supported as MP3 and M4A for tagging*

## One File At A Time

*I only want to copy one file into the staging folder and let tune-shifter process it*

## Human Readable Bandcamp State

...

## Nested Folders

*I want to copy a folder of folders into the staging folder and let tune-shifter process all files in all sub-folders*

## Does Bandcamp auto-download actually work?  Test poll_interval_minutes.




## Configurable Album Art Search

*I want to be able to configure where album art is retrieved from*

The default setting should be "default" and retrieve the art from musicbrainz.

*I want to be able to retrieve album art from bandcamp*

The configuration setting for this should be `"bandcamp"`

*I want to be able to retrieve album art from iTunes / Apple Music*

The configuration setting for this should be `"apple"`.

*I want to be able to retrieve album art from Spotify*

The configuration setting for this should be `"spotify"`.

*I want to be able to retrieve album art from Qobuz*

The configuration settings for this should be `"qobuz"`

## GUI / menu bar app for sync status

## Allow a user to verify tags before they're written

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
