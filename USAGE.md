Quick start:

  tune-shifter                  # first-run setup + start daemon
  tune-shifter install-service  # auto-start at login

Drop a ZIP or folder into your staging directory — tune-shifter handles the rest.

Bandcamp sync:

  tune-shifter sync                  # download new purchases
  tune-shifter sync --mark-synced    # first time: skip existing collection

Spotlight shortcut (macOS):

  tune-shifter install-shortcut      # opens Shortcuts app — click "Add Shortcut"
                                     # then find "Bandcamp Sync" in Spotlight
                                     # run 'tune-shifter sync' in a terminal first
                                     # to set up Bandcamp credentials
