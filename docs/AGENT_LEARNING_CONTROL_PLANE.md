# Agent Learning Control Plane

Το learning control plane είναι κοινή, τοπική μνήμη αξιολόγησης για τους agents.
Καταγράφει αμετάβλητες αποφάσεις τη στιγμή που λαμβάνονται, μεταγενέστερα outcomes,
εκδόσεις features και ασφαλή JSON models. Δεν εκτελεί συναλλαγές και δεν επικοινωνεί
με εξωτερική υπηρεσία.

## Ροή

1. Οι Babis, Giannis και Petros καταγράφουν decision-time episodes μετά την ολοκλήρωση
   ενός έγκυρου run.
2. Ένα outcome προστίθεται μόνο όταν ωριμάσει ο δηλωμένος χρονικός ορίζοντας και
   συνοδεύεται από confidence και evidence hash.
3. Ένα εκπαιδευμένο JSON model εισέρχεται ως `candidate`.
4. Η μετάβαση `candidate -> shadow -> champion` είναι αποκλειστικά χειροκίνητη.
5. Drift ή υποβάθμιση μπορούν να μεταφέρουν το μοντέλο σε `frozen` ή `rejected`.

Το ξεχωριστό objective ανά agent αποτρέπει τη χρήση της χρηματιστηριακής απόδοσης ως
μοναδικού κριτηρίου. Οι validation και risk agents, για παράδειγμα, θα αξιολογούνται
με defect detection και drawdown/risk metrics αντίστοιχα.

## Εντολές

```powershell
.\run_py.cmd agent_learning_control_plane.py init
.\run_py.cmd agent_learning_control_plane.py summary
.\run_py.cmd agent_learning_control_plane.py dashboard
```

Το dashboard γράφεται στο `output/dashboards/agent_learning_latest.html`.
