# Gridlocked workspace redesign

Scope: the existing React map workspace, camera directory, route planner, playback controls, and new Gemini driver panel. Mode: Operate. Keep the working camera and street selection interactions while making model control visible.

## Direction contract

**Point of view.** A New York street operations desk: calm paper controls around a dark geographic scene. The car and its actual decision are the focus.

**First viewport.** A solid topbar identifies Gridlocked, offers Map/Intersection/Follow and the two clear actions Gemini driver and Traffic demo. One 326px rail contains Cameras, Route, Driver. The live map occupies the remainder, with small telemetry and playback instruments.

**Typography and material.** Warm off-white panels, charcoal text, taxi-yellow action emphasis. DM Sans for human-readable interface text; IBM Plex Mono for time and numeric telemetry. Flat rows and measured rules; no glass, gradients, or decorative dashboard cards.

**Signature interaction.** Give Gemini the wheel: a visible reasoning/status panel switches the map to follow the ego, freezes simulation during the model call, then drives up to three simulated seconds from the real response. Stop cancels the run and invalidates late results.

**Motion grammar.** Geographic transitions preserve context. Follow tracks the ego heading; interface feedback uses brief color changes. Reduced motion disables decorative movement. A model failure never animates a fabricated response.

**Responsive and proof.** On narrow screens the task rail overlays the map and the perspective controls occupy a second row. Driver Start dismisses the rail to reveal the car; View Gemini decisions reopens it. Verify desktop1440×900, mobile390×844, and compact731×727. The camera network, planned route, actual model decisions and stopped states are evidence, not marketing placeholders.
