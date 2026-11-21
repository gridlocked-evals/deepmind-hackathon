# Gridlocked

Gridlocked is a local NYC traffic simulation workspace for hackathon builders and demo viewers. People browse public roadway cameras, select street locations, plan a driving route, watch authored traffic, and give Gemini control of a simulated ego vehicle.

The primary surface is an operating interface: map and vehicle state lead; camera selection, route planning, and agent decisions are three task views in one rail. A visible Traffic demo restores the original 25-vehicle scene. Camera media, synthetic traffic, route geometry, and model control must retain distinct provenance.

The current model driver consumes structured simulated observations and chooses longitudinal target speed. Steering, safety constraints, and scene physics are deterministic. Camera perception and the visual Genie controller are separate integration work. Do not imply that this UI measures real-world safety or reconstructs traffic from camera imagery.

Desktop is the main demonstration environment. The same controls must remain usable at 390px mobile and the compact in-app browser width. Real camera imagery and rendered map/vehicles provide the visuals; no decorative generated imagery is needed.
