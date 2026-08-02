from dotenv import load_dotenv

from services.ai import generate_ai_response


load_dotenv()


FOCUS_OPTIONS = {
    "growth": [
        "Lead Generation",
        "SEO Strategy",
        "Content Marketing",
        "Customer Acquisition"
    ],
    "competitor": [
        "Pricing Strategy",
        "Feature Comparison",
        "Competitive Advantages",
        "Customer Reviews"
    ],
    "product": [
        "Product Positioning",
        "Target Audience",
        "Go-to-Market Strategy",
        "Pricing Strategy"
    ],
    "sales": [
        "Sales Funnel",
        "Cold Outreach",
        "Lead Qualification",
        "Closing Strategy"
    ],
    "default": [
        "Marketing Ideas",
        "Customer Acquisition",
        "Competitive Positioning",
        "Growth Opportunities"
    ]
}


def get_focus_options(research_goal):
    goal = research_goal.lower()

    if "growth" in goal or "marketing" in goal:
        return FOCUS_OPTIONS["growth"]

    if "competitor" in goal or "competition" in goal:
        return FOCUS_OPTIONS["competitor"]

    if "product" in goal:
        return FOCUS_OPTIONS["product"]

    if "sales" in goal:
        return FOCUS_OPTIONS["sales"]

    return FOCUS_OPTIONS["default"]


def choose_focus_area(research_goal):
    options = get_focus_options(research_goal)

    print("\nSuggested Focus Areas")
    print("----------------------")

    for index, option in enumerate(options, start=1):
        print(f"{index}. {option}")

    print("5. Custom")

    choice = input("\nEnter choices separated by commas (example: 1,3,4): ")
    selected_numbers = choice.replace(" ", "").split(",")

    if "5" in selected_numbers and len(selected_numbers) > 1:
        print("Custom cannot be combined with other options.")
        return choose_focus_area(research_goal)

    if "5" in selected_numbers:
        custom_focus = input("Enter your custom focus: ")
        return custom_focus

    selected_focus = []

    for number in selected_numbers:
        if number in ["1", "2", "3", "4"]:
            selected_focus.append(options[int(number) - 1])

    if len(selected_focus) == 0:
        print("Invalid selection. Please try again.")
        return choose_focus_area(research_goal)

    return ", ".join(selected_focus)


def build_prompt(company, industry, target_audience, research_goal, focus_area):
    prompt = f"""
Create a clear business research and growth strategy report.

Company: {company}
Industry: {industry}
Target Audience: {target_audience}
Research Goal: {research_goal}
Special Focus Areas: {focus_area}

Include:
1. Executive Summary
2. Overall Priority Score out of 10 with reasoning
3. Company Overview
4. Possible Competitors
5. Strengths
6. Weaknesses
7. Opportunities
8. Threats
9. Marketing Ideas
10. Suggested Next Steps
11. Quick Wins
12. 30-Day Action Plan
13. Recommended AI Tools Stack

For the 30-Day Action Plan, structure it as:
Week 1:
Week 2:
Week 3:
Week 4:

For the Recommended AI Tools Stack, suggest practical tools that fit the company, industry, audience, and research goal.

Rules:
- Be practical.
- Do not invent exact statistics.
- Clearly mention when something is an assumption.
- Keep the report structured and easy to read.
- Prioritize the selected special focus areas.
- Make the output useful for a founder, marketer, or growth team.
"""

    return prompt


def generate_report():
    company = input("Company name: ")
    industry = input("Industry: ")
    target_audience = input("Target audience: ")
    research_goal = input("Research goal: ")

    focus_area = choose_focus_area(research_goal)

    prompt = build_prompt(
        company,
        industry,
        target_audience,
        research_goal,
        focus_area
    )

    try:
        report = generate_ai_response(
            prompt,
            system_prompt=(
                "You are a practical business research and growth "
                "strategy analyst."
            ),
        )
        print("\n===== LeadLens AI Report =====")
        print(report)
    except RuntimeError as error:
        print("\nSomething went wrong while generating the report.")
        print("Error:", error)


def main_menu():
    while True:
        print("\n===== LeadLens AI =====")
        print("1. Generate Company Research Report")
        print("2. Exit")

        choice = input("Choose an option: ")

        if choice == "1":
            generate_report()
        elif choice == "2":
            print("Goodbye!")
            break
        else:
            print("Invalid option. Please choose 1 or 2.")


main_menu()
