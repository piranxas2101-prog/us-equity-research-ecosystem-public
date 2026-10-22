# Σχέδιο: ανανέωση στοιχείων μόνο όταν αλλάζουν (filing-driven refresh)

Κατάσταση: **πρόταση προς έγκριση** (2026-09-29). Δεν έχει γραφτεί κώδικας για αυτό.

## 1. Τι μετρήσαμε (πλήρες run 24–25/9)

| Βήμα | Διάρκεια | Πηγή μέτρησης |
|---|---|---|
| Babis stage 1, 13.241 σύμβολα | 11,5 ώρες (3,1 δευτ./σύμβολο) | `pipeline_runtime_20260925_012252.md` |
| ↳ τιμές αγοράς | 2,5 ώρες | ίδιο |
| ↳ yfinance info + news | 3,9 ώρες | ίδιο |
| ↳ SEC πρόσφατες καταθέσεις | 0,6 ώρες | ίδιο |
| Giannis triage + information ledger | ~20 λεπτά | ώρες αρχείων |
| **`fundamental_control_plane_batch.py`, 1.068 εταιρείες** | **~14,9 ώρες (~50 δευτ./εταιρεία)** | ώρες αρχείων 01:41 → 16:33 |
| Petros, deep research, πάγωμα packet, traders, Sokratis, receipt | ~40 λεπτά | ώρες αρχείων |

Το μεγαλύτερο κόστος δεν είναι ο Babis. Είναι το batch που για κάθε εταιρεία της shortlist
κατεβάζει ολόκληρο το SEC companyfacts (`collect_sec_financials` → `fetch_companyfacts`),
με το rate limit της SEC. Υπάρχει ήδη μηχανισμός επαναχρησιμοποίησης, αλλά ταιριάζει
μόνο πακέτα **της ίδιας ημερομηνίας** (`p.as_of = as_of`). Έτσι κάθε μέρα όλα
ξανακατεβαίνουν, ενώ τα companyfacts αλλάζουν μόνο όταν η εταιρεία καταθέσει κάτι.
Οι 170 «blocked» του run ήταν κυρίως αρνήσεις του rate limit της SEC.

## 2. Βασική αρχή

Τα στοιχεία από καταθέσεις αλλάζουν μόνο με νέα κατάθεση. Την ημέρα που δεν
υπάρχει νέα κατάθεση για μια εταιρεία, τα στοιχεία της υπολογίζονται από την
αποθηκευμένη πηγή και όχι από νέο κατέβασμα. Το αποτέλεσμα πρέπει να είναι
**ίδιο** με αυτό ενός νέου κατεβάσματος. Όπου δεν μπορούμε να το εγγυηθούμε,
κατεβάζουμε κανονικά.

## 3. Τα κομμάτια

### Α. Ημερολόγιο καταθέσεων SEC (ένα αίτημα ανά ημέρα)
- Κάθε run διαβάζει το ημερήσιο ευρετήριο της SEC (`edgar/daily-index/.../form.YYYYMMDD.idx`)
  για κάθε εργάσιμη μέρα από το προηγούμενο run. Αυτό είναι ένα μικρό αρχείο ανά ημέρα.
- Γράφει σε νέο πίνακα `sec_filing_event(cik, form, filed_date, accession, source_sha256)`
  τις φόρμες 10-K, 10-Q, 20-F, 40-F, 6-K, 8-K και τις τροποποιήσεις τους (/A).
- Αν λείπει κάποια μέρα ή αποτύχει η λήψη, **καμία** εταιρεία δεν θεωρείται
  «χωρίς νέα κατάθεση» για εκείνο το διάστημα. Όλες κατεβαίνουν όπως σήμερα, οπότε
  η αποτυχία δεν κάνει ποτέ τα στοιχεία να φαίνονται πιο φρέσκα απ' ό,τι είναι.

### Β. Αποθήκη companyfacts και επαναχρησιμοποίηση (το μεγάλο κέρδος)
**Κατάσταση (2026-09-29):** υλοποιήθηκε (`companyfacts_cache.py`) σε κατάσταση `populate`,
δηλαδή αποθηκεύει χωρίς να επαναχρησιμοποιεί. Περνά σε `reuse` αφού η
`python companyfacts_cache.py shadow` δείξει ίδια εξαγωγή σε 50 εταιρείες.
Νέα λήψη γίνεται μόνο με 10-K, 10-Q, 20-F, 40-F, 6-K και τις τροποποιήσεις τους.
Ένα 8-K δεν προκαλεί νέα λήψη, γιατί τα companyfacts αλλάζουν με την περιοδική
αναφορά που ακολουθεί.
- Κάθε companyfacts που κατεβαίνει αποθηκεύεται συμπιεσμένο ανά CIK (`cache/sec_companyfacts/`),
  μαζί με το sha256 και την τελευταία κατάθεση που περιέχει.
- Στο επόμενο run, αν για αυτό το CIK **δεν υπάρχει** `sec_filing_event` μετά την
  τελευταία κατάθεση που περιέχει, τρέχουμε το ίδιο `extract_companyfacts(raw, σημερινό as_of)`
  πάνω στο αποθηκευμένο αρχείο. Έτσι οι κανόνες φρεσκάδας («πόσο παλιά είναι η
  τελευταία περίοδος») υπολογίζονται με τη σημερινή ημερομηνία, όπως σε νέο κατέβασμα.
- Υποχρεωτικό νέο κατέβασμα όταν:
  - υπάρχει νέα κατάθεση
  - το αποθηκευμένο αρχείο είναι παλιότερο από **30 ημέρες**, ως δίχτυ ασφαλείας
  - αλλάξει η έκδοση του extractor ή του contract
- Κάθε πακέτο γράφει ρητά `source: cached_companyfacts` ή `fresh_download`, με την
  ημερομηνία λήψης. Τα πακέτα μένουν immutable: η επαναχρησιμοποίηση φτιάχνει νέο
  πακέτο που δείχνει στην πηγή του, όπως κάνει ήδη το `v3-reuse-receipt`.
- Εκτίμηση κέρδους, προς επιβεβαίωση: τις περισσότερες μέρες καταθέτουν ~2–5% των
  εταιρειών, οπότε το βήμα των 14,9 ωρών πέφτει στα λεπτά. Στις εβδομάδες των
  αποτελεσμάτων θα κρατά περισσότερο.
- Χώρος στον δίσκο: ~1.100 εταιρείες × λίγα MB συμπιεσμένα, δηλαδή μερικές εκατοντάδες MB.

### Γ. Babis stage 1: yfinance info μόνο όπου χρειάζεται
- **ETF, funds και SPAC:** έγινε ήδη (2026-09-29). Βγήκαν από το κεντρικό run σε
  δικές τους κατηγορίες, `etf_lane` (6.014) και `spac_lane` (435), μέσω του
  `universe.separate_lane_routes`. Μαζί με τους 1.090 τίτλους που ανήκουν σε εκδότη,
  ο Babis σαρώνει 5.706 σύμβολα αντί για 13.245.
- **Εταιρείες:** τα στοιχεία που βγαίνουν από καταθέσεις (margins, growth, ROE, ROA,
  cash, debt, D/E, current ratio κ.λπ.) κρατιούνται με την ημερομηνία τους. Ξαναζητούνται
  όταν υπάρξει νέα κατάθεση, και ξανά 3–5 μέρες αργότερα, γιατί το yfinance αργεί
  να ενημερωθεί. Υποχρεωτική ανανέωση σε 7 μέρες το αργότερο.
- **Λόγοι που εξαρτώνται από την τιμή** (P/E, forward P/E, P/S, P/B, market cap): η
  τιμή υπολογίζεται κάθε μέρα από το αποθηκευμένο στοιχείο × σημερινή τιμή / τιμή τη
  στιγμή της αποθήκευσης. Κάθε τέτοιος λόγος γράφει ότι προέκυψε έτσι (`price_scaled_from`).
- Το forward P/E εξαρτάται και από τις εκτιμήσεις των αναλυτών, που αλλάζουν χωρίς
  κατάθεση. Γι' αυτό η υποχρεωτική ανανέωση σε 7 μέρες ισχύει και εδώ.
- **News:** μόνο για τη shortlist και τις εταιρείες που παρακολουθούμε. Για τις
  υπόλοιπες, ως σήμα λειτουργεί η νέα κατάθεση.

### Δ. Χρονομέτρηση ανά βήμα
Κάθε βήμα του `run_daily_dual_watchlist.ps1` γράφει ώρα έναρξης και λήξης στο
manifest. Έτσι δεν χρειάζεται να συμπεραίνουμε τις διάρκειες από τις ώρες των αρχείων.

## 4. Κανόνες που δεν σπάνε
- Το άγνωστο μένει άγνωστο. Κάθε τιμή έχει ημερομηνία και πηγή: `fresh`, `cached` ή `price_scaled`.
  Αν δεν υπάρχει έγκυρη πηγή, η τιμή λείπει με αιτία. Ποτέ δεν μπαίνει παλιά τιμή σαν σημερινή.
- Τα πακέτα και τα manifests μένουν immutable και hashed.
- Όταν κάτι δεν είναι σίγουρο, κατεβάζουμε όπως σήμερα. Η αποτυχία ενός μηχανισμού
  δεν μειώνει ποτέ την ποιότητα, μόνο την ταχύτητα.
- Κάθε κομμάτι μπαίνει με διακόπτη στο `scanner_config.json` και είναι κλειστό μέχρι
  να περάσει τη σύγκριση του §5.
- Κανένα LLM. Καμία αλλαγή σε κατώφλια scoring, risk ή promotion gates.

## 5. Πώς θα ελεγχθεί πριν ανοίξει
1. Unit tests για κάθε κομμάτι: ευρετήριο SEC με κενές μέρες, reuse έναντι νέας κατάθεσης, όριο 30 ημερών, price scaling.
2. **Σύγκριση σε σκιά:** σε 50 εταιρείες τρέχουμε και τους δύο τρόπους. Στο Β τα
   packets πρέπει να βγαίνουν **ίδια** πεδίο προς πεδίο. Στο Γ οι λόγοι τιμής
   πρέπει να διαφέρουν λιγότερο από ένα μικρό όριο που θα συμφωνήσουμε. Αν αποκλίνει
   κάτι, ο διακόπτης μένει κλειστός.
3. Ένα πλήρες run με ανοιχτούς διακόπτες. Μετράμε χρόνο και ελέγχουμε το receipt.

## 6. Σειρά υλοποίησης
1. Δ: χρονομέτρηση. Είναι μικρή και ανεξάρτητη.
2. Α: ημερολόγιο καταθέσεων.
3. Β: αποθήκη companyfacts και reuse. Είναι το μεγάλο κέρδος.
4. Γ: Babis stage 1.

## 7. Αποφάσεις ιδιοκτήτη (2026-09-29)
- Όρια φρεσκάδας: companyfacts **30 μέρες**, yfinance στοιχεία **7 μέρες**. Εγκρίθηκαν.
- Λόγοι τιμής από αποθηκευμένο στοιχείο × τιμή, με ρητή σήμανση. Εγκρίθηκε.
- ETF και SPAC σε δικές τους κατηγορίες, εκτός κεντρικού run. Εγκρίθηκε και έγινε.

## 8. Επόμενο για τα SPAC (χρειάζεται το §3Α)
Ένα SPAC γίνεται ενδιαφέρον όταν ανακοινώσει συγχώνευση. Με το ημερολόγιο
καταθέσεων (§3Α), οι καταθέσεις S-4, F-4, DEFM14A, 425 και 8-K ενός SPAC θα το
σημειώνουν ως «ανακοίνωσε συγχώνευση» και θα το εμφανίζουν στον Κέντρο Ελέγχου.
Όταν η συγχώνευση ολοκληρωθεί και η εταιρεία μπει στη λίστα εταιρειών της SEC,
περνά αυτόματα στην κανονική έρευνα. Αυτό ισχύει ήδη, γιατί ένα σύμβολο που
είναι στη λίστα εταιρειών δεν πηγαίνει ποτέ σε ξεχωριστή κατηγορία.

## 2026-10-10: gate Γ judges only the fields the rules use (owner's decision)

On 2026-10-09 the gate compared 23 companies; in two of them `sharesOutstanding` had moved more than 2% (buybacks or
issuance), so the share within tolerance was 91% < 95% and the info store went back to `populate`: stage 1 asked
yfinance for every company (2.8 h instead of ~0.5 h). No Babis rule reads the share count. The owner chose that only
the fields used by `fundamental_rules` decide (today: returnOnEquity, profitMargins, grossMargins, revenueGrowth,
earningsGrowth, debtToEquity, currentRatio, forwardPE); the 95% / 2% and median-3% thresholds stay as they were for
those. Other fields remain in the shadow report. Code: `refresh_gate.rule_fields`, `decide_stage1_info(shadow, fields)`.

## 2026-10-12: drifted companies are refreshed one by one (owner's decision)

On 2026-10-10 two or three of 26 companies' earningsGrowth moved more than 2% (91% < 95%) and the whole store went
back to `populate`; the next night only 17 copies were comparable, so it stayed there (Babis ~5 h on three nights).
A replay on 2026-10-12 of 60 reusable copies found no drift: the flips came from single companies in small samples.
Now a company whose rule fields drift has only its own copy dropped (`Stage1InfoCache.invalidate`, listed as
`refreshed_companies` in the gate report) and the store stays in `reuse`; it flips to `populate` only when more than
20% of the compared companies drift on a rule field (`STORE_WIDE_DRIFT`), or a price field drifts in the median.
The comparison also samples only copies of reusable age (3-7 days) instead of the oldest ones, which could not be
reused anyway and used up the sample.
