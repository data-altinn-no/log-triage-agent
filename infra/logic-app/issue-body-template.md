# GitHub issue body template (emitted by the Logic App)

The Logic App must create issues with **this exact section structure**.
`agents/services/parser.py` parses the sections by `###` heading names.

```
### Exception
@{items('ForEach')?['exceptionType']}

### Message
@{items('ForEach')?['message']}

### Cloud role
@{items('ForEach')?['cloud_RoleName']}

### Operation
@{items('ForEach')?['operation']}

### Request path
@{items('ForEach')?['requestPath']}

### Timestamp
@{items('ForEach')?['firstSeen']}

### Correlation id
@{items('ForEach')?['correlationId']}

### Occurrences
@{items('ForEach')?['count_']}

### Stack trace
```
@{items('ForEach')?['stackTrace']}
```
```

## Title

```
[prod] @{items('ForEach')?['exceptionType']} in @{items('ForEach')?['cloud_RoleName']}
```

## Labels

At minimum: `auto-triage`, `prod`, `source:app-insights`.
The agent will add severity / category / routing labels after analysis.
