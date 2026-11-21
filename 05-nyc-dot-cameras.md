# NYC DOT Traffic Cameras

## The data (verified Nov 14–15, 2026)

- **Camera list:** `GET https://webcams.nyctmc.org/api/cameras`, no API key. Returns `id`, `name`, `latitude`, `longitude`, `area`, `isOnline`, `imageUrl`.
- **Snapshot:** `GET https://webcams.nyctmc.org/api/cameras/<id>/image` returns a 352×240 JPEG. In a test it changed within 3 seconds.
- **Count:** 974 cameras, about 874 online. Manhattan 376, Queens 213, Brooklyn 206, Staten Island 98, Bronx 81.
- **Intersections:** 735 of the 873 online cameras are named like an intersection ("Broadway @ 42 St").
- **Midtown:** 78 online cameras between roughly 33rd–59th St and 3rd–10th Ave. The densest 1 km squares have 16–22 cameras.
- **Live speed feed:** NYC DOT traffic speeds at `https://data.cityofnewyork.us/resource/i4gi-tjb9.json`, updated in near real time. Mostly highways and major roads, with speed, travel time and a polyline per segment.
- **Limits:** low-res snapshots, one camera per intersection. Good for counting cars and estimating queues. Can't follow individual cars between cameras, and too low-res to read faces or plates.

## Camera + agent ideas

1. **Ask the City.** A judge asks a live question ("Where is it raining?", "Find a truck blocking a bike lane"). A swarm of agents, one per neighborhood, scans all cameras in parallel; verifier agents pull fresh frames to confirm or reject each hit.
   - **Wow:** about 30 seconds later the NYC map lights up with live thumbnails tagged verified or rejected.
2. **Prediction Arena: a world model of NYC.** Agents predict each scene 5 minutes ahead ("this Canal St. backup clears").
   - **Wow:** a live scoreboard grading each prediction against the real frame when it arrives. Badly calibrated agents are "fired" and replaced, so the swarm visibly improves. A benchmark no model could have trained on, because answers are seconds old.
3. **Traffic Detective.** Record frames from the morning. On stage: "Why did the Queensboro Bridge jam at 5:12?"
   - **Wow:** agents trace backward through time and across cameras and show the cause step by step, with frames played in order like evidence.
4. **311 Autopilot.** Agents patrol for hazards (flooding, downed tree, dark signal, blocked bike lane), confirm across several frames, and draft a 311 report with photos. Draft only, never submit.
   - **Wow:** "Since 9 AM, this swarm found 14 real issues across the five boroughs," with evidence and a measured false-alarm rate.
5. **Ambulance Green Wave.** Agents watch every camera along an emergency route and re-route as conditions change.
   - **Wow:** a segment turns red because a camera shows gridlock, and the route redraws live with the footage explaining why. Same "re-plan and justify it" pattern as eagle in SF.
6. **Live Eval Arena for vision models.** Every 30 seconds, auto-generate questions about frames seconds old ("How many buses are on 34th & 7th?"). Several models answer; a verifier swarm grades them.
   - **Wow:** a live leaderboard. "The only benchmark that can't leak, because the answers didn't exist a minute ago."
7. **City Pulse Narrator.** A newsroom of agents (one reporter per borough plus an editor) broadcasts a voiced "NYC right now" segment with camera shots. Fun, but lightest on evals.

**Pick at the time:** combine #1 and #2.

## Before building
- **Fetch politely:** cache frames and rotate through cameras rather than hitting all of them every second.
- **Keep it about traffic, not people:** frame it as roads and city conditions, not tracking individuals. Judges will ask.
- **Start recording early:** anything that looks back in time needs frames saved before the demo.
