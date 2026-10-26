# Power BI dashboard for production managers: build guide

`python run.py` writes the tables below to `powerbi/data/`. This builds the report in Power BI Desktop (about 30 minutes).

## 1. Load the data

| Table | Grain | Used for |
| --- | --- | --- |
| `fact_machine_day` | machine × day (36,600 rows) | OEE, availability, performance, quality, output, scrap, downtime hours |
| `dim_machine` | machine | model, age, breakdowns, MTBF, current risk score |
| `fact_events` | one breakdown, alarm, repair or planned replacement | MTBF, event timelines, reactive share |
| `loss_tree` | one OEE loss | loss waterfall |
| `downtime_pareto` | one cause | Pareto chart |
| `risk_scores` | machine | inspection list |
| `errors_before_failures` | component × alarm | warning-alarm matrix |
| `hypothesis_tests` | one test | statistics page |

In Power Query, add `event_date = Date.From([datetime])` to `fact_events`. Create a date table and mark it:

```DAX
dim_date = ADDCOLUMNS ( CALENDAR ( DATE ( 2015, 1, 1 ), DATE ( 2016, 1, 1 ) ),
    "Month", FORMAT ( [Date], "MMM YYYY" ), "MonthStart", DATE ( YEAR ( [Date] ), MONTH ( [Date] ), 1 ),
    "Weekday", FORMAT ( [Date], "ddd" ) )
```

## 2. Relationships

- `dim_machine[machineID]` 1 → * `fact_machine_day`, `fact_events`, `risk_scores`
- `dim_date[Date]` 1 → * `fact_machine_day[date]`, `fact_events[event_date]`

## 3. Measures

Paste [`measures.dax`](measures.dax) into a `_Measures` table.

## 4. Report pages

| Page | Visuals | KPI |
| --- | --- | --- |
| **Overview** | KPI cards; OEE gauge (target 85% world class); OEE, A, P, Q by month (line); loss waterfall from `loss_tree` | OEE, Downtime hours, Production output, Defect rate, MTBF |
| **Downtime Pareto** | columns `Cause Hours` + line `Cumulative Cause %`; downtime by machine (bar, top 15); machine × month matrix with colour scale | Downtime hours |
| **Reliability** | MTBF by component and model (bar); breakdowns by machine age (column); `errors_before_failures` matrix (component × alarm, conditional colour on lift); event timeline per machine (drill-through) | MTBF |
| **Quality** | defect rate by model and month; scrap units by machine | Defect rate |
| **Maintenance planning** | `risk_scores` table sorted by risk with days since each component replacement; reactive vs planned maintenance (donut) | Machines at high risk |

Slicers on every page: machine model, machine age band, month. Add drill-through from a machine to a machine-detail page.

Save as `powerbi/Manufacturing_OEE.pbix`.
