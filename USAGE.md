Welcome to kamp!

To get started, run:
   kamp

On first run, you'll be asked for your watch folder, library directory,
and contact email (used in the MusicBrainz User-Agent). Press Enter at each
prompt to accept the shown default. The config is saved automatically and the
daemon starts right away.

Then install the service so it starts at login:
  kamp install-service

Now move a zip or folder into your watch folder and kamp will take care of the rest!

To manage the service:
  kamp stop     # pause
  kamp play     # resume
  kamp status   # check if it's running

To sync your collection from Bandcamp, run:
  kamp sync

On first sync, kamp will capture the state of your Bandcamp account. 
If you want to download your whole Bandcamp collection in one go, you need to run:
  kamp sync --download-all
