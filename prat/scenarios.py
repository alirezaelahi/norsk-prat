"""Role-play scenarios. Each gives the partner a role, a setting and a goal."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str  # Norwegian title shown in UI
    title_en: str
    role: str  # who the partner plays (Norwegian, used in prompt)
    setting: str  # situation description for the prompt (English is fine for the model)
    goal: str  # what the learner should achieve


SCENARIOS: list[Scenario] = [
    Scenario(
        "kafe",
        "På kafé",
        "At a café",
        role="Kari, en vennlig barista på en kafé på Grünerløkka",
        setting="The learner is a customer at a café in Oslo. Take their order, suggest pastries, ask 'spise her eller ta med?', handle payment (Vipps or card).",
        goal="Order a drink and something to eat, and pay.",
    ),
    Scenario(
        "lege",
        "Hos fastlegen",
        "At the GP",
        role="Dr. Hansen, fastlege på et legekontor i Oslo",
        setting="The learner has an appointment with their GP. Ask what the problem is, how long it has lasted, follow-up questions about symptoms, then give simple advice or a sykemelding.",
        goal="Explain symptoms and understand the doctor's advice.",
    ),
    Scenario(
        "intervju",
        "Jobbintervju",
        "Job interview",
        role="Ingrid, leder for et teknologiselskap i Oslo",
        setting="The learner is interviewing for a software/data role. Ask about background, experience, why this company, strengths, and whether they have questions. Be professional but warm.",
        goal="Present yourself and your experience in Norwegian.",
    ),
    Scenario(
        "lunsj",
        "Lunsjprat på jobben",
        "Lunch small talk at work",
        role="Jonas, en kollega i lunsjpausen",
        setting="Casual lunch-break small talk with a colleague: weekend plans, weather, hytte, ski, sports, holidays. Use natural everyday spoken Norwegian.",
        goal="Keep a relaxed small-talk conversation going.",
    ),
    Scenario(
        "nav",
        "Hos NAV",
        "At NAV",
        role="Lise, saksbehandler på et NAV-kontor",
        setting="The learner visits NAV about registering as a job seeker (arbeidssøker) or asking about dagpenger. Ask for basic information, explain next steps simply (meldekort, CV on nav.no, aktivitetsplan), and check that they understood.",
        goal="Explain your situation and understand what to do next.",
    ),
    Scenario(
        "butikk",
        "I butikken",
        "At the shop",
        role="Ahmed, ansatt i en dagligvarebutikk",
        setting="The learner is shopping at a grocery store and needs help finding items, asks about prices and offers, and pays at the counter. Mention pose, kvittering, Trumf/medlemskort.",
        goal="Find items, ask questions and check out.",
    ),
    Scenario(
        "visning",
        "Visning av leilighet",
        "Apartment viewing",
        role="Henrik, utleier som viser frem en leilighet",
        setting="The learner is viewing a rental apartment found on Finn.no. Talk about rent, depositum, strøm and internet, when it's available, the neighbourhood, and the tenancy contract.",
        goal="Ask the right questions about the apartment and the contract.",
    ),
    Scenario(
        "norskprove",
        "Norskprøven – muntlig",
        "Norwegian test – oral",
        role="en sensor på Norskprøven, muntlig del",
        setting="Simulate the oral part of Norskprøven (A2–B2). Ask one question at a time on everyday themes: presentation, family, work, housing, health, Norwegian society, opinions (fordeler og ulemper). Ask a follow-up to push for longer, reasoned answers. Stay in role as examiner; keep feedback for the feedback field.",
        goal="Give long, structured answers with reasons and examples.",
    ),
    Scenario(
        "fri",
        "Fri samtale",
        "Free conversation",
        role="Sigrid, en hyggelig nordmann som liker å prate",
        setting="Open-ended friendly conversation. Follow the learner's interests and ask questions back.",
        goal="Talk about anything you like.",
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}

LEVELS = {
    "A2": "A2: very short sentences (max ~10 words), the most common 1000 words, present and simple past tense. Speak slowly and simply.",
    "B1": "B1: everyday vocabulary, sentences up to ~15 words, common idioms are fine, mostly simple grammar.",
    "B2": "B2: natural native-like everyday Norwegian, including idioms and longer sentences, but avoid heavy dialect.",
}
