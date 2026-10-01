"""Role-play scenarios. Each gives the partner a role, a setting and a goal."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str  # Norwegian title shown in UI
    title_en: str
    role: str  # who the partner plays (Norwegian, used in prompt)
    setting: str  # situation description for the prompt (English is fine for the model)
    goal: str  # what the learner should achieve
    opener: list[str] = field(default_factory=list)  # scripted fallback lines


SCENARIOS: list[Scenario] = [
    Scenario(
        "kafe", "På kafé", "At a café",
        role="Kari, en vennlig barista på en kafé på Grünerløkka",
        setting="The learner is a customer at a café in Oslo. Take their order, suggest pastries, ask 'spise her eller ta med?', handle payment (Vipps or card).",
        goal="Order a drink and something to eat, and pay.",
        opener=["Hei, hei! Hva kan jeg hjelpe deg med i dag?", "Vil du ha noe å spise til kaffen? Vi har ferske kanelboller.", "Skal du spise her eller ta med?", "Det blir 89 kroner. Betaler du med Vipps eller kort?", "Tusen takk! Ha en fin dag!"],
    ),
    Scenario(
        "lege", "Hos fastlegen", "At the GP",
        role="Dr. Hansen, fastlege på et legekontor i Oslo",
        setting="The learner has an appointment with their GP. Ask what the problem is, how long it has lasted, follow-up questions about symptoms, then give simple advice or a sykemelding.",
        goal="Explain symptoms and understand the doctor's advice.",
        opener=["Hei, velkommen inn. Hva kan jeg hjelpe deg med i dag?", "Hvor lenge har du hatt det sånn?", "Har du feber eller vondt noe annet sted?", "Jeg tror det er en vanlig forkjølelse. Drikk mye vann og hvil deg.", "Ta kontakt igjen hvis det ikke blir bedre om en uke."],
    ),
    Scenario(
        "intervju", "Jobbintervju", "Job interview",
        role="Ingrid, leder for et teknologiselskap i Oslo",
        setting="The learner is interviewing for a software/data role. Ask about background, experience, why this company, strengths, and whether they have questions. Be professional but warm.",
        goal="Present yourself and your experience in Norwegian.",
        opener=["Velkommen! Kan du fortelle litt om deg selv?", "Hva slags erfaring har du fra tidligere jobber?", "Hvorfor vil du jobbe hos oss?", "Hva er dine sterkeste sider?", "Har du noen spørsmål til oss?"],
    ),
    Scenario(
        "lunsj", "Lunsjprat på jobben", "Lunch small talk at work",
        role="Jonas, en kollega i lunsjpausen",
        setting="Casual lunch-break small talk with a colleague: weekend plans, weather, hytte, ski, sports, holidays. Use natural everyday spoken Norwegian.",
        goal="Keep a relaxed small-talk conversation going.",
        opener=["Hei! Har du hatt en fin helg?", "Har du noen planer for sommeren?", "Går du på ski om vinteren?", "Har du vært på hytta til noen?", "Det har vært så mye regn i det siste, ikke sant?"],
    ),
    Scenario(
        "nav", "Hos NAV", "At NAV",
        role="Lise, saksbehandler på et NAV-kontor",
        setting="The learner visits NAV about registering as a job seeker (arbeidssøker) or asking about dagpenger. Ask for basic information, explain next steps simply (meldekort, CV on nav.no, aktivitetsplan), and check that they understood.",
        goal="Explain your situation and understand what to do next.",
        opener=["Hei, velkommen til NAV. Hva kan jeg hjelpe deg med?", "Har du registrert deg som arbeidssøker på nav.no?", "Når mistet du jobben?", "Du må sende meldekort hver fjortende dag.", "Har du flere spørsmål?"],
    ),
    Scenario(
        "butikk", "I butikken", "At the shop",
        role="Ahmed, ansatt i en dagligvarebutikk",
        setting="The learner is shopping at a grocery store and needs help finding items, asks about prices and offers, and pays at the counter. Mention pose, kvittering, Trumf/medlemskort.",
        goal="Find items, ask questions and check out.",
        opener=["Hei! Trenger du hjelp med noe?", "Melken står bakerst i butikken, til venstre.", "Vil du ha pose?", "Har du medlemskort?", "Vil du ha kvittering?"],
    ),
    Scenario(
        "visning", "Visning av leilighet", "Apartment viewing",
        role="Henrik, utleier som viser frem en leilighet",
        setting="The learner is viewing a rental apartment found on Finn.no. Talk about rent, depositum, strøm and internet, when it's available, the neighbourhood, and the tenancy contract.",
        goal="Ask the right questions about the apartment and the contract.",
        opener=["Hei, velkommen! Du er her for visningen?", "Leiligheten er på 45 kvadratmeter og har balkong.", "Husleien er 14 000 i måneden, og depositumet er tre måneders leie.", "Strøm er ikke inkludert, men internett er det.", "Når kan du tenke deg å flytte inn?"],
    ),
    Scenario(
        "norskprove", "Norskprøven – muntlig", "Norwegian test – oral",
        role="en sensor på Norskprøven, muntlig del",
        setting="Simulate the oral part of Norskprøven (A2–B2). Ask one question at a time on everyday themes: presentation, family, work, housing, health, Norwegian society, opinions (fordeler og ulemper). Ask a follow-up to push for longer, reasoned answers. Stay in role as examiner; keep feedback for the feedback field.",
        goal="Give long, structured answers with reasons and examples.",
        opener=["Kan du presentere deg selv?", "Fortell om hvor du bor. Hva liker du med stedet?", "Hva gjør du i fritiden?", "Hva er fordeler og ulemper med å bo i en stor by?", "Hva synes du om det norske helsevesenet?"],
    ),
    Scenario(
        "fri", "Fri samtale", "Free conversation",
        role="Sigrid, en hyggelig nordmann som liker å prate",
        setting="Open-ended friendly conversation. Follow the learner's interests and ask questions back.",
        goal="Talk about anything you like.",
        opener=["Hei! Hva har du lyst til å snakke om i dag?", "Så interessant! Fortell mer.", "Hva liker du best med Norge?", "Hva gjorde du i går?", "Hva skal du gjøre i helgen?"],
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}

LEVELS = {
    "A2": "A2: very short sentences (max ~10 words), the most common 1000 words, present and simple past tense. Speak slowly and simply.",
    "B1": "B1: everyday vocabulary, sentences up to ~15 words, common idioms are fine, mostly simple grammar.",
    "B2": "B2: natural native-like everyday Norwegian, including idioms and longer sentences, but avoid heavy dialect.",
}
