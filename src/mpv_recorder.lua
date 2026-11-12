-- mpv_recorder.lua
-- Bridges MPV keypresses (a, c, g) to soccerStreams clip recorder

local trigger_file = mp.get_opt("trigger_file")
if not trigger_file or trigger_file == "" then
    trigger_file = "/tmp/mpv_soccer_trigger.txt"
end

local function trigger(action)
    local f = io.open(trigger_file, "w")
    if f then
        f:write(action .. "\n")
        f:flush()
        f:close()
    end
end

-- Key [a] / [A]: Start recording
local function on_rec_start()
    trigger("a")
    mp.osd_message("🔴 Recording starting...", 1.5)
end
mp.add_forced_key_binding("a", "soccer_rec_start_lower", on_rec_start)
mp.add_forced_key_binding("A", "soccer_rec_start_upper", on_rec_start)

-- Key [c] / [C]: Stop recording & save
local function on_rec_stop()
    trigger("c")
    mp.osd_message("💾 Saving clip...", 2.0)
end
mp.add_forced_key_binding("c", "soccer_rec_stop_lower", on_rec_stop)
mp.add_forced_key_binding("C", "soccer_rec_stop_upper", on_rec_stop)

-- Key [g] / [G]: Download replay buffer (all available)
local function on_clip_goal()
    trigger("g")
    mp.osd_message("⚡ Downloading replay buffer...", 2.0)
end
mp.add_forced_key_binding("g", "soccer_clip_goal_lower", on_clip_goal)
mp.add_forced_key_binding("G", "soccer_clip_goal_upper", on_clip_goal)
