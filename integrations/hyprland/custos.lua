-- Custos : à charger depuis ~/.config/hypr/hyprland.lua
-- (copier dans ~/.config/hypr/hyprland/custos.lua puis ajouter require("hyprland.custos")).

hl.on("hyprland.start", function()
    hl.exec_cmd("custos daemon")
end)

-- Ouvrir la bulle pour poser une question
hl.bind("SUPER + SHIFT + A", hl.dsp.exec_cmd("custos ask"))
-- Demander à Custos à propos du texte sélectionné
hl.bind("SUPER + SHIFT + E", hl.dsp.exec_cmd("custos ask --selection"))
