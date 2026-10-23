-- Custos : démarrage et raccourcis.
-- À charger depuis ~/.config/caelestia/hypr-user.lua :
--   dofile(os.getenv("HOME") .. "/Documents/Projets/custos/integrations/hyprland/custos.lua")

local custos = os.getenv("HOME") .. "/.local/bin/custos"

hl.on("hyprland.start", function()
    hl.exec_cmd(custos .. " daemon")
end)

-- Ouvrir la bulle au clavier (G comme gardien) ; sinon, un clic sur la mascotte.
hl.bind("SUPER + G", hl.dsp.exec_cmd(custos .. " ask"))
