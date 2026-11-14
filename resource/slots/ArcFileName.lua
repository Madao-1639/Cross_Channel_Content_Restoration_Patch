-- ArcFileName.lua — slot-enabled rewrite of the vanilla mapping table.
--
-- Slots: patch.arc -> patch2.arc -> patch3.arc, later slot wins (same convention as 9nine).
-- A slot only carries the members it overrides; anything absent falls back to vanilla,
-- so a slot can be a minimal package (e.g. one CG) instead of a full archive.
--
-- Why member-level lookup is mandatory: sub_4D54F0 resolves the SAME requested name across
-- all four roots. If "patch.arc" were returned unconditionally, any member absent from that
-- arc would fail outright (0x33 hangs) instead of falling back to Chip2.arc.

local SLOTS = { "patch.arc", "patch2.arc", "patch3.arc" }
local cache = {}     -- arc path -> raw blob (false when unreadable/absent)

local function arcBlob(arc)
    local b = cache[arc]
    if b ~= nil then return b end
    local f = io.open(arc, "rb")
    b = false
    if f then
        b = f:read("a")
        f:close()
        if not b or #b < 8 then b = false end
    end
    cache[arc] = b
    return b
end

-- Member names are stored as UTF-16LE; build the same byte pattern from an ASCII name.
local function utf16le(s)
    local out = {}
    for i = 1, #s do
        out[#out + 1] = s:sub(i, i)
        out[#out + 1] = "\0"
    end
    return table.concat(out)
end

local function hasMember(blob, name)
    -- Search ONLY the TOC region (header + name table). Scanning the whole blob would let a
    -- byte pattern inside image data produce a false positive.
    if #blob < 8 then return false end
    local _, table_size = string.unpack("<I4I4", blob)
    local toc = blob:sub(1, 8 + table_size)
    return string.find(toc, utf16le(name) .. "\0\0", 1, true) ~= nil
end

local function slotHit(name)
    local probe = string.lower(name)
    for i = #SLOTS, 1, -1 do
        local blob = arcBlob(SLOTS[i])
        if blob and hasMember(blob, probe) then return SLOTS[i] end
    end
    return nil
end

function getRioArcFileName(name)
    local hit = slotHit(name)
    if hit then return hit end
    local low = string.lower(string.sub(name, 1, 8))
    if low == "mainmenu" then return "Rio.arc" end
    if low == "mainpart" then return "Rio.arc" end
    return "Rio.arc"
end

function getGraphicsArcFileName(a, name)
    local hit = slotHit(name)
    if hit then return hit end
    local low = string.lower(string.sub(name, 1, 4))
    if low == "sys_" then return "SysGraphic.arc" end
    if low == "efmsk" then return "Graphic.arc" end
    if low == "bgcc" then return "Chip1.arc" end
    if low == "evcc" then return "Chip2.arc" end
    return "Graphic.arc"
end

function getModelArcFileName(a, name)
    local hit = slotHit(name)
    if hit then return hit end
    return "Model.arc"
end

function getSoundArcFileName(a, name)
    local hit = slotHit(name)
    if hit then return hit end
    local low = string.lower(name)
    if string.find(low, "sysvoice") then return "SysVoice.arc" end
    if string.find(low, "char") then return "Voice.arc" end
    if string.find(low, "bgv") then return "Se.arc" end
    if string.find(low, "bgm") then return "Bgm.arc" end
    return "SysVoice.arc"
end
