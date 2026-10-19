"""The 30 day measurement protocol: checkpoints, topics and timings.

Three topic types per checkpoint (personal, opinion, describe), 30 seconds of
preparation, 2 minutes of speaking. Day 0 and day 2 use different topics of the
same types, so that the difference between them estimates natural variability
(topic included). Days 15 and 30 use new topics plus one repeated topic from
day 0, to tell memorisation from real change.
"""

from __future__ import annotations

PREP_SECONDS = 30
SPEAK_SECONDS = 120

TOPICS: dict[str, dict[str, str]] = {
    "a-personal": {"type": "personal", "text": "Tell me about a time something did not go as planned, and what you did about it."},
    "a-opinion": {"type": "opinion", "text": "Some people say working from home is better than working in an office. What do you think, and why?"},
    "a-describe": {"type": "describe", "text": "Explain what you do, or what you study, to a curious twelve year old."},
    "n-personal": {"type": "personal", "text": "Describe a person who has influenced the way you think, and how they did it."},
    "n-opinion": {"type": "opinion", "text": "Do people today rely too much on their phones? Give your view with examples."},
    "n-describe": {"type": "describe", "text": "Describe your ideal weekend, from the moment you wake up until you go to sleep."},
    "b-personal": {"type": "personal", "text": "Tell me about a decision you were unsure about, and how it turned out."},
    "b-opinion": {"type": "opinion", "text": "Should cities ban cars from their centers? Explain your position."},
    "b-describe": {"type": "describe", "text": "Describe a place you know well so that someone who has never been there can picture it."},
    "c-personal": {"type": "personal", "text": "Tell me about a skill you learned the hard way, and what the process was like."},
    "c-opinion": {"type": "opinion", "text": "Is it better to specialize deeply in one field or to know a little about many? Argue your side."},
    "c-describe": {"type": "describe", "text": "Explain how to cook a meal you know well, from shopping to serving."},
}

CHECKPOINTS: list[dict] = [
    {
        "id": "base0",
        "label": "Day 0 baseline",
        "due_day": 0,
        "topics": ["a-personal", "a-opinion", "a-describe"],
        "repeats": [],
        "hint": "Your starting point. Do not prepare beyond the 30 seconds given.",
    },
    {
        "id": "base2",
        "label": "Day 2 baseline",
        "due_day": 2,
        "topics": ["n-personal", "n-opinion", "n-describe"],
        "repeats": [],
        "hint": "Same task types, different topics. The gap with day 0 estimates your natural variability.",
    },
    {
        "id": "day15",
        "label": "Day 15 retest",
        "due_day": 15,
        "topics": ["b-personal", "b-opinion", "b-describe"],
        "repeats": ["a-opinion"],
        "hint": "New topics, plus one topic repeated from day 0.",
    },
    {
        "id": "day30",
        "label": "Day 30 retest",
        "due_day": 30,
        "topics": ["c-personal", "c-opinion", "c-describe"],
        "repeats": ["a-describe"],
        "hint": "New topics, plus one topic repeated from day 0.",
    },
]

CHECKPOINT_BY_ID = {c["id"]: c for c in CHECKPOINTS}


def topics_for_checkpoint(checkpoint_id: str) -> list[dict]:
    cp = CHECKPOINT_BY_ID[checkpoint_id]
    items = [
        {"topic_id": t, "is_repeat": False, **TOPICS[t]} for t in cp["topics"]
    ] + [
        {"topic_id": t, "is_repeat": True, **TOPICS[t]} for t in cp["repeats"]
    ]
    return items


# 4/3/2 drill: the same content told three times, in 4, 3 and then 2 minutes.
DRILL_SECONDS = (240, 180, 120)
DRILL_PREP_SECONDS = (30, 10, 10)
DRILL_TOPICS: list[dict[str, str]] = [
    {"id": "d1", "text": "Tell the story of a trip, a project or an event that did not go as expected."},
    {"id": "d2", "text": "Explain how something you know well works, step by step."},
    {"id": "d3", "text": "Describe a typical day in your life right now, and what you would change about it."},
    {"id": "d4", "text": "Tell me about a teacher or mentor who mattered to you, and what you learned from them."},
    {"id": "d5", "text": "Explain a problem you solved recently and how you approached it."},
    {"id": "d6", "text": "Describe a place you would take a visitor to, and why."},
    {"id": "d7", "text": "Tell me about a mistake that taught you something."},
    {"id": "d8", "text": "Explain a concept from your field to someone with no background in it."},
]
