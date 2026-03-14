Welcome to tune-shifter!

To get started, create the config by running:
   tune-shifter

This will create a config file. Define your `staging` and `library` folders.

Then install the service:
  tune-shifter install-service

Now move a zip or folder into your staging folder and tune-shifter will take care of the rest!

To sync your collection from Bandcamp, run:
  tune-shifter sync

If most of your Bandcamp collection is already in your local library, run this first to avoid redownloading everything:
  tune-shifter sync --mark-synced
