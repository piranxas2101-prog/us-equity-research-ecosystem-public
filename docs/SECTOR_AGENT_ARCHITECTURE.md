# Sector-agent research architecture

## Απόφαση

Ο Μπάμπης είναι η μοναδική αρχή ταυτότητας και ταξινόμησης. Κάθε εταιρικό
σύμβολο με έγκυρο taxonomy node δρομολογείται σε έναν από έντεκα sector agents.
Οι υποκατηγορίες δεν δημιουργούν ξεχωριστούς agents· ενεργοποιούν versioned
frameworks μέσα στον agent του αντίστοιχου κλάδου.

Σύμβολο χωρίς έγκυρο `sector.subcategory` node μπαίνει στην ειδική κατηγορία
`unresolved_classification` και δρομολογείται στον δωδέκατο agent
`sector_unresolved_classification`. Ο agent συγκεντρώνει τεκμηριωμένο company
profile και προτείνει υποψήφιο sector/subcategory. Δεν γράφει taxonomy· η
πρόταση επιστρέφει στον Μπάμπη, που παραμένει η μοναδική αρχή ταξινόμησης.

## Ροή

`symbol registry -> Babis classification -> sector agent -> Sector Context Packet -> Giannis financial analysis -> future synthesis`

`unresolved symbol -> unresolved-classification agent -> evidence-backed proposal -> Babis review -> normal sector queue`

Οι sector agents αναλύουν επιχειρηματικό μοντέλο, αλυσίδα αξίας, αγορά,
ανταγωνισμό, λειτουργικούς drivers, ειδικά KPIs, κινδύνους και catalysts. Δεν
βαθμολογούν συνολικά τις οικονομικές καταστάσεις, δεν κάνουν τεχνική ανάλυση
και δεν δίνουν οδηγίες συναλλαγών.

Ο Γιάννης είναι η κεντρική αρχή χρηματοοικονομικής ανάλυσης. Παραλαμβάνει το
ίδιο-run Sector Context Packet και normalized οικονομικά στοιχεία, και αναλύει
ανάπτυξη, περιθώρια, ποιότητα κερδών, cash flow, ισολογισμό, ρευστότητα,
κεφαλαιακές ανάγκες, dilution και κατανομή κεφαλαίου. Δεν μεταβάλλει τα
upstream στοιχεία· παράγει ξεχωριστό Financial Analysis Packet.

## Agents και υποκατηγορίες

| Sector agent | Υποκατηγορίες |
| --- | --- |
| `sector_communication_services` | media, platforms, telecom |
| `sector_consumer_discretionary` | goods_retail, mobility, services_housing |
| `sector_consumer_staples` | household_distribution, products |
| `sector_energy` | hydrocarbons, support_fuels |
| `sector_financials` | banking_credit, insurance, markets |
| `sector_healthcare` | care, therapeutics |
| `sector_industrials` | business_services, capital_goods, transport |
| `sector_materials` | chemicals, forest_packaging, resources |
| `sector_real_estate` | development_services, ownership |
| `sector_technology` | hardware_services, semiconductors, software |
| `sector_utilities` | generation, networks |
| `sector_unresolved_classification` | unresolved_classification (ειδική κατηγορία επίλυσης, όχι κανονικός κλάδος) |

## Production gates

1. Ολοκληρωμένο και ελεγμένο Babis classification run.
2. Έγκυρο `sector_agent_registry.json` έναντι της ενεργής taxonomy.
3. Κοινό packet schema και same-run lineage.
4. Source references, timestamps, confidence και evidence gaps σε κάθε claim.
5. Πιλοτικός Technology agent σε bounded δείγμα πριν ενεργοποιηθούν οι άλλοι.
6. Review report και ρητή έγκριση πριν από κάθε production promotion.

Μετά από επιτυχή ολοκλήρωση του canonical Babis pipeline, το
`sector_ecosystem_integration` εκτελείται μία φορά και idempotently. Εγγράφει
τους 11 κανονικούς sector agents και τον ειδικό resolution agent, τις πληροφοριακές κατηγορίες, τις sector queues και το contract
handoff προς τον Γιάννη. Αποτυχία του activation καταγράφεται χωριστά και δεν
μετατρέπει μια ολοκληρωμένη ταξινόμηση σε ψευδή επιτυχία ανάλυσης.

Δεν επιτρέπονται full-universe επαναλήψεις χωρίς νέα ή αλλαγμένη πληροφορία.
Μετά το αρχικό backfill, οι agents καταναλώνουν μόνο νέα, μεταβλημένα ή ρητά
ζητημένα σύμβολα από τις sector queues.

## Έλεγχος αρχιτεκτονικής

```powershell
.\run_py.cmd us_equity_monitor.py sector-agent validate-registry
.\run_py.cmd us_equity_monitor.py sector-agent route --node technology.software.applications.enterprise
```

Η εντολή `scaffold` δημιουργεί μόνο explicit-unknown packet για μελλοντική
συλλογή στοιχείων. Δεν παράγει ανάλυση, δεν καλεί API και δεν συμπληρώνει
πεδία με εικασίες.

Και οι έντεκα agents υλοποιούνται στο package `sector_agents`. Η εντολή
`sector-agent analyze --input INPUT.json --out PACKET.json` επιλέγει αυτόματα
τον σωστό agent από το taxonomy node, απαιτεί πηγές για κάθε συμπληρωμένο
analysis block και παράγει ελεγμένο Sector Context Packet. Unsupported claims
απορρίπτονται αντί να υποβαθμίζονται σιωπηρά.

Η εντολή `sector-agent analyze-unresolved` παράγει
`ecosystem.unresolved_classification_packet.v1`. Κάθε συμπληρωμένο profile
field και κάθε classification candidate απαιτεί source ids. Το packet έχει
`classification_write_allowed=false` και παραδίδεται αποκλειστικά στον Μπάμπη.
