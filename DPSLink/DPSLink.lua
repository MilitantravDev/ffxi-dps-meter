_addon.name = 'DPSLink'
_addon.author = 'MilitantravDev'
_addon.version = '1.0.0'
_addon.commands = {'dpslink', 'dpsl'}

-- Streams parsed combat actions from this character to the external DPS Meter app over UDP.
-- Load it on every character you want feeding the app; the app de-duplicates actions
-- that several characters saw at the same time.

require('luau')
local config = require('config')
local res = require('resources')
local socket = require('socket')

local defaults = {
    host = '127.0.0.1',
    port = 24250,
    heartbeat = 2,
}
local settings = config.load(defaults)

-------------------------------------------------------------------------------
-- UDP
-------------------------------------------------------------------------------
local udp = nil
local sent, errors = 0, 0

local function open_socket()
    if udp then pcall(function() udp:close() end) end
    udp = socket.udp()
    udp:settimeout(0)
    local ok, err = udp:setpeername(settings.host, settings.port)
    if not ok then
        windower.add_to_chat(167, '[DPSLink] socket error: ' .. tostring(err))
        udp = nil
    end
end

-- Minimal JSON encoder (strings, numbers, booleans, arrays, objects)
local encode
local function esc(s)
    return (s:gsub('[%c"\\]', function(c)
        if c == '"' then return '\\"' elseif c == '\\' then return '\\\\'
        elseif c == '\n' then return '\\n' else return string.format('\\u%04x', c:byte()) end
    end))
end
encode = function(v)
    local t = type(v)
    if t == 'string' then return '"' .. esc(v) .. '"'
    elseif t == 'number' then return (v == v and v ~= math.huge and v ~= -math.huge) and tostring(v) or '0'
    elseif t == 'boolean' then return tostring(v)
    elseif t == 'table' then
        if #v > 0 or next(v) == nil then
            local out = {}
            for i = 1, #v do out[i] = encode(v[i]) end
            return '[' .. table.concat(out, ',') .. ']'
        end
        local out = {}
        for k, x in pairs(v) do
            if type(x) ~= 'function' then out[#out + 1] = '"' .. esc(tostring(k)) .. '":' .. encode(x) end
        end
        return '{' .. table.concat(out, ',') .. '}'
    end
    return 'null'
end

local function send(tbl)
    if not udp then return end
    local player = windower.ffxi.get_player()
    if not player then return end
    tbl.src = player.name
    local ok = udp:send(encode(tbl))
    if ok then sent = sent + 1 else errors = errors + 1 end
end

-------------------------------------------------------------------------------
-- Message classification
-------------------------------------------------------------------------------
local function set(t) local s = {} for _, v in ipairs(t) do s[v] = true end return s end

local DAMAGE_MSG = set{1, 2, 67, 77, 110, 157, 185, 187, 196, 197, 227, 252, 264, 265, 274,
                       317, 352, 353, 379, 522, 576, 577, 648, 650, 747, 748, 802}
local CRIT_MSG   = set{67, 353}
local MISS_MSG   = set{15, 63, 158, 188, 324, 354}
local SC_MSG     = set{288, 289, 290, 291, 292, 293, 294, 295, 296, 297, 298, 299, 300, 301, 302,
                       385, 386, 387, 388, 389, 390, 391, 392, 393, 394, 395, 396, 397, 398,
                       732, 767, 768, 769, 770}
local ADDEFF_MSG = set{161, 163, 229}
local SPIKE_MSG  = set{33, 44, 536}

local CATEGORY = {
    [1] = 'melee', [2] = 'ranged', [3] = 'ws', [4] = 'spell', [6] = 'ability',
    [11] = 'ability', [13] = 'ability', [14] = 'ability', [15] = 'ability',
}

local function res_name(tbl, id)
    local r = tbl and tbl[id]
    return r and (r.english or r.en or r.name)
end

local function ability_name(category, param)
    if category == 1 then return 'Melee'
    elseif category == 2 then return 'Ranged'
    elseif category == 3 then return res_name(res.weapon_skills, param) or res_name(res.job_abilities, param)
    elseif category == 4 then return res_name(res.spells, param)
    elseif category == 11 then return res_name(res.monster_abilities, param) or res_name(res.job_abilities, param)
    else return res_name(res.job_abilities, param) or res_name(res.monster_abilities, param) end
end

-------------------------------------------------------------------------------
-- Party tracking
-------------------------------------------------------------------------------
local allies, allies_time = {}, 0

local function refresh_allies()
    allies = {}
    allies_time = os.clock()
    local party = windower.ffxi.get_party()
    local player = windower.ffxi.get_player()
    if not party then return end
    for slot, m in pairs(party) do
        if type(slot) == 'string' and type(m) == 'table' and m.mob then
            local mob = m.mob
            local kind
            if player and mob.id == player.id then kind = 'me'
            elseif mob.is_npc then kind = 'trust'
            elseif slot:sub(1, 1) == 'p' then kind = 'party'
            else kind = 'alliance' end
            allies[mob.id] = { id = mob.id, name = mob.name, kind = kind }
            if mob.pet_index and mob.pet_index > 0 then
                local pet = windower.ffxi.get_mob_by_index(mob.pet_index)
                if pet then allies[pet.id] = { id = pet.id, name = pet.name, kind = 'pet', owner = mob.name } end
            end
        end
    end
end

local function ally(id)
    local a = allies[id]
    if not a and os.clock() - allies_time > 1 then
        refresh_allies()
        a = allies[id]
    end
    return a
end

local function enemy(id)
    if allies[id] then return nil end
    local mob = windower.ffxi.get_mob_by_id(id)
    if not mob or not mob.is_npc or mob.in_party or mob.in_alliance then return nil end
    return mob
end

-------------------------------------------------------------------------------
-- Action parsing -> one datagram per action
-------------------------------------------------------------------------------
local function actor_fields(pkt, a)
    pkt.aid, pkt.an, pkt.ak, pkt.ao = a.id, a.name, a.kind, a.owner
end

windower.register_event('action', function(act)
    local cat = CATEGORY[act.category]
    local actor = ally(act.actor_id)

    if actor and cat then
        local ab = ability_name(act.category, act.param) or ('#' .. tostring(act.param))
        local hits = {}
        for _, target in ipairs(act.targets) do
            local mob = enemy(target.id)
            if mob then
                for _, a in ipairs(target.actions) do
                    local m = a.message
                    if DAMAGE_MSG[m] then
                        hits[#hits + 1] = { mid = mob.id, mn = mob.name, c = cat, ab = ab, d = a.param,
                                            f = CRIT_MSG[m] and 'crit' or nil }
                    elseif MISS_MSG[m] then
                        hits[#hits + 1] = { mid = mob.id, mn = mob.name, c = cat, ab = ab, f = 'miss' }
                    end
                    if a.has_add_effect and (a.add_effect_param or 0) > 0 then
                        local am = a.add_effect_message
                        if SC_MSG[am] then
                            hits[#hits + 1] = { mid = mob.id, mn = mob.name, c = 'sc', ab = 'SC: ' .. ab, d = a.add_effect_param }
                        elseif ADDEFF_MSG[am] then
                            hits[#hits + 1] = { mid = mob.id, mn = mob.name, c = 'addeff', ab = 'Additional Effect', d = a.add_effect_param }
                        end
                    end
                end
            end
        end
        if #hits > 0 then
            local pkt = { t = 'act', cat = act.category, p = act.param, hits = hits }
            actor_fields(pkt, actor)
            send(pkt)
        end
    elseif not actor and act.category == 1 then
        -- enemy melee into an ally with spikes up: credit the spike damage to the ally
        local mob = enemy(act.actor_id)
        if not mob then return end
        for _, target in ipairs(act.targets) do
            local def = ally(target.id)
            if def then
                local hits = {}
                for _, a in ipairs(target.actions) do
                    if a.has_spike_effect and SPIKE_MSG[a.spike_effect_message] and (a.spike_effect_param or 0) > 0 then
                        hits[#hits + 1] = { mid = mob.id, mn = mob.name, c = 'spikes', ab = 'Spikes', d = a.spike_effect_param }
                    end
                end
                if #hits > 0 then
                    local pkt = { t = 'act', cat = 0, p = 0, hits = hits }
                    actor_fields(pkt, def)
                    send(pkt)
                end
            end
        end
    end
end)

-------------------------------------------------------------------------------
-- Heartbeat: lets the app list connected characters
-------------------------------------------------------------------------------
local last_hb = 0
local function heartbeat()
    local player = windower.ffxi.get_player()
    if not player then return end
    local info = windower.ffxi.get_info()
    local zone = res.zones[info.zone]
    local party = {}
    for _, a in pairs(allies) do party[#party + 1] = { id = a.id, n = a.name, k = a.kind, o = a.owner } end
    send({
        t = 'hb', id = player.id,
        job = player.main_job and (player.main_job .. (player.main_job_level or '') ..
              (player.sub_job and ('/' .. player.sub_job .. (player.sub_job_level or '')) or '')) or nil,
        zone = zone and zone.english or tostring(info.zone),
        party = party,
    })
end

windower.register_event('prerender', function()
    local t = os.clock()
    if t - last_hb >= settings.heartbeat then
        last_hb = t
        refresh_allies()
        heartbeat()
    end
end)

windower.register_event('zone change', function()
    send({ t = 'zone' })
end)

windower.register_event('load', function()
    open_socket()
    refresh_allies()
end)

windower.register_event('unload', 'logout', function()
    send({ t = 'bye' })
end)

-------------------------------------------------------------------------------
-- Commands
-------------------------------------------------------------------------------
windower.register_event('addon command', function(cmd, ...)
    cmd = cmd and cmd:lower() or 'status'
    local args = {...}
    if cmd == 'host' and args[1] then
        settings.host = args[1]
        if tonumber(args[2]) then settings.port = tonumber(args[2]) end
        settings:save()
        open_socket()
        windower.add_to_chat(207, string.format('[DPSLink] sending to %s:%d', settings.host, settings.port))
    elseif cmd == 'port' and tonumber(args[1]) then
        settings.port = tonumber(args[1])
        settings:save()
        open_socket()
        windower.add_to_chat(207, '[DPSLink] port ' .. settings.port)
    elseif cmd == 'reset' then
        send({ t = 'reset' })
        windower.add_to_chat(207, '[DPSLink] asked the app to reset.')
    else
        windower.add_to_chat(207, string.format('[DPSLink] -> %s:%d  packets sent %d, errors %d  (%s)',
            settings.host, settings.port, sent, errors, udp and 'socket open' or 'NO SOCKET'))
        windower.add_to_chat(207, '  //dpsl host <ip> [port]   //dpsl port <n>   //dpsl reset')
    end
end)
