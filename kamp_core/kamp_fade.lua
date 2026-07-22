-- kamp_fade.lua: click-free fades via a persistent afade filter re-armed by af-command.

-- af-command addresses the filter by its BARE mpv label (no "@" -- the "@" is only
-- the labelling syntax in the --af-append definition) and routes to the inner ffmpeg
-- filter instance by name ("afade"). Verified empirically against mpv v0.41.0 / ffmpeg
-- 8.1: label "@kampfade" or target "all" both return "error running command"; only
-- bare label + inner-name target succeeds. This was the bug behind the "timer, not a
-- fade" behaviour -- the command silently failed so the gain never moved.
local LABEL = "kampfade"
local MUTE_LABEL = "kampmute"  -- KAMP-559: independent afade for user mute
local TARGET = "afade"
local DUR = 0.15            -- seconds; must match the filter's duration= in _start_mpv
local PARKED = "1000000000" -- start_time far in the future => filter passes at unity
local MARGIN = 0.05         -- device latency beyond the soft audio buffer

-- Cancels a pending post-fade pause / resume-unmute when a newer request arrives.
local pause_gen = 0
-- True once a stop has run its course (seek to 0). Resume-after-stop is a hard reset
-- (start at unity, no fade-in) since a fade-in across that seek is unreliable.
local stopped = false

local function afcmd(command, argument)
    mp.commandv("af-command", LABEL, command, argument, TARGET)
end

-- Park the fade far in the future so it never triggers: passes audio at unity gain.
local function park()
    afcmd("type", "out")
    afcmd("start_time", PARKED)
end

-- mpv hands ~audio-buffer seconds of audio to the output device before it is heard; that
-- audio is already past the filter and can no longer be faded. So a fade is anchored at
-- the FILTER HEAD (audio-pts + audio-buffer + MARGIN), not the audible position --
-- otherwise a short fade lands entirely on already-buffered audio and is inaudible (a
-- 3 s fade was audible only because it overflowed the buffer). Verified vs mpv 0.41.
local function output_lead()
    return (mp.get_property_number("audio-buffer", 0.2) or 0.2) + MARGIN
end

-- Arm a fade ("in"/"out") starting `ahead` seconds past the current audio PTS.
local function arm(direction, ahead)
    local pts = mp.get_property_number("audio-pts", 0) or 0
    -- afade rejects a NEGATIVE start_time (the af-command fails, leaving the previous
    -- start_time); audio-pts reads slightly negative right after a seek-while-paused.
    if pts < 0 then pts = 0 end
    afcmd("type", direction)
    afcmd("start_time", tostring(pts + ahead))
end

mp.register_script_message("kamp-pause", function()
    pause_gen = pause_gen + 1
    local my = pause_gen
    mp.set_property_bool("mute", false)  -- clear any pending resume-unmute gate
    local lead = output_lead()
    arm("out", lead)
    -- Pause only after the faded audio has drained through the output buffer, so the
    -- pause lands on silence rather than cutting off still-full-volume buffered audio.
    mp.add_timeout(lead + DUR, function()
        if my == pause_gen then mp.set_property("pause", "yes") end
    end)
end)

mp.register_script_message("kamp-resume", function()
    pause_gen = pause_gen + 1  -- cancel any pending post-fade pause
    local my = pause_gen
    if stopped then
        -- Resume after a stop is a hard reset: start at unity, no fade-in.
        stopped = false
        mp.set_property_bool("mute", false)
        park()
        mp.set_property("pause", "no")
        return
    end
    -- The output buffer may hold FULL-VOLUME audio: mpv resets the afade filter to its
    -- unity definition on every seek/load, so a seek-while-paused -- or the load_paused
    -- that restores a mid-track position on app start -- refills the buffer at full
    -- volume, and the fade filter (which sits before the buffer) cannot retroactively
    -- fade it. So gate the OUTPUT with `mute` (which sits AFTER the filter and survives
    -- seeks) while that buffer drains, then unmute exactly as the fade-in window reaches
    -- the speakers. Result: resume always fades in from silence regardless of what was
    -- buffered. arm() anchors the fade-in at the filter head (one lead ahead), so it
    -- reaches output right when the buffer finishes draining.
    local lead = output_lead()
    mp.set_property_bool("mute", true)
    arm("in", lead)
    mp.set_property("pause", "no")
    mp.add_timeout(lead, function()
        if my == pause_gen then mp.set_property_bool("mute", false) end
    end)
    -- Safety net: force unity after the fade window so an edge-case fade-in can never
    -- strand the gain below full.
    mp.add_timeout(lead + DUR, function()
        if my == pause_gen then park() end
    end)
end)

mp.register_script_message("kamp-stop", function()
    pause_gen = pause_gen + 1
    local my = pause_gen
    mp.set_property_bool("mute", false)
    local lead = output_lead()
    arm("out", lead)
    mp.add_timeout(lead + DUR, function()
        if my == pause_gen then
            mp.set_property("pause", "yes")
            mp.command("seek 0 absolute")
            park()
            stopped = true
        end
    end)
end)

-- User mute (KAMP-559) — a SECOND, independent afade (kampmute) so mute and the
-- pause/resume fade above never fight over one filter. kamp-mute fades the output to
-- silence and holds it; kamp-unmute fades it back in. mpv resets afade filters to
-- unity on every seek/load, so the mute is re-applied on playback-restart while held.
local user_muted = false

local function mutecmd(command, argument)
    mp.commandv("af-command", MUTE_LABEL, command, argument, TARGET)
end

-- Arm the mute filter ("out"/"in"), anchored at the filter head like arm() above so
-- the fade is audible rather than landing inside already-buffered audio.
local function mute_arm(direction)
    local pts = mp.get_property_number("audio-pts", 0) or 0
    if pts < 0 then pts = 0 end
    mutecmd("type", direction)
    mutecmd("start_time", tostring(pts + output_lead()))
end

mp.register_script_message("kamp-mute", function()
    user_muted = true
    mute_arm("out")
end)

mp.register_script_message("kamp-unmute", function()
    user_muted = false
    mute_arm("in")
end)

-- A fresh load resets the filter to its unity definition anyway; re-park to be explicit
-- and clear the stopped flag so the next resume fades in normally.
mp.register_event("file-loaded", function()
    park()
    stopped = false
end)

-- Seeks and loads reset both afade filters to unity; re-apply the user mute so it
-- survives them (the pause/resume kampfade re-applies via its own kamp-resume path).
mp.register_event("playback-restart", function()
    if user_muted then mute_arm("out") end
end)
