# Market research & ideas

## What Stockline does today
YC S25, founded by two X22 students. They describe themselves as an "AI-native ERP for food wholesalers" (fish, meat, cheese, produce, flowers).
1. **Capture.** Orders from WhatsApp, email, calls/voicemail, SMS and photos of handwritten lists are turned into ERP lines. It understands "the usual" or "same as last week, double the tomatoes".
2. **Company brain.** Each customer has a history: what they buy, at what price, how often, and whether they care more about price or quality.
3. **Agents.** Selling, pricing and purchasing agents: follow up with quiet customers, win back churned ones, send promos, sell short-dated stock, suggest purchase orders. **Nothing goes out until a person approves it.**

Style: dark and industrial, short declarative copy ("Always ahead.", "Admin disappears."), dense data tables and customer cards. Whatever we build should feel like a new tab in their product.

## Competitors
Choco is the big one: it says it serves 21k+ distributors, sells an AI order-capture tool, and bought the French startup Goot in July 2026. Fresho, Pepper and Rekki are the others.
➡️ Order capture is already crowded. **Build what happens after the order comes in.**

## French context we can use
- **RNM / FranceAgriMer** publishes official daily wholesale prices, including Rungis. Part of it is open data on data.gouv.fr.
- **Anti-waste law:** wholesalers above €50M turnover must offer donation agreements to food charities, and donated food needs at least 48h left before its use-by date (DLC).
- **E-invoicing:** every company must be able to receive e-invoices since 1 Sept 2026, and SMEs must issue them from Sept 2027.

## Idea shortlist (final pick on Monday, after we see the real challenge)
| # | Idea | Pitch |
|---|---|---|
| A | **Shortage & substitution agent** | At 4am it finds what's short for tomorrow's orders, offers each restaurant a substitute that fits its profile on WhatsApp, collects the answers and updates the orders |
| B | **Anti-waste agent for short-dated stock** | Ranks lots by risk of expiring unsold, offers them first to the customers most likely to buy at a discount, and sends anything within 48h of its DLC to a generated donation agreement |
| C | **Margin guard** | Loads daily Rungis/RNM prices and supplier price lists, flags customer prices below a margin floor, drafts new price lists |
| D | **Complaints & credit-note agent** | "2kg short, cod not fresh" plus a photo leads to matching the order line and lot, a drafted credit note, and a quality score per supplier |
| E | **Outbound voice sales rep** | Calls restaurants that haven't ordered by their usual time: "Chef, the usual?" |
| F | **Collections + e-invoicing** | Polite payment reminders on WhatsApp, and a credit hold on the account when an order arrives |

Favourites: **A** or **B**, with C's price data inside.
