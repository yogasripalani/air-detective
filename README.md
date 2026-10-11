# air-detective


> Don't just see that the air got worse. Find out why.

## Problem
When air pollution rises, ordinary people can see the number but not the reason. Raw dashboards don't say what changed, where, or when.

## Solution
Pick a city and a date. Air Detective compares that day with the previous day, the same weekday last week and the last 28 days, flags unusual changes, and writes an evidence report covering weather shifts, time-of-day patterns and whether the rise was local or regional.

## How the analysis works
- **Anomaly detection:** z-score against a 28-day baseline, plus a minimum absolute rise (≥10 µg/m³) so clean cities don't trigger false alarms.
- **Weather evidence:** weak wind, higher humidity and missing rain are compared with the baseline.
- **Time of day:** the PM2.5 peak hour is checked against commute hours (an indicator of traffic, not proof).
- **Where:** the city is compared with four points about 55 km away, to separate regional episodes from local sources.
- **Explainable by design:** every finding is a rule with visible numbers, with no black box.

## Architecture
![Architecture](docs/architecture.png)

| AWS service | Role |
|---|---|
| AWS Lambda | Runs the analysis (Python) |
| Amazon API Gateway (HTTP API) | Public REST endpoint with CORS |
| Amazon DynamoDB | Caches reports for instant repeat searches |
| AWS Amplify Hosting | Hosts the web app |
| Amazon CloudWatch | Logs and debugging |
| AWS IAM | Least-privilege access for Lambda to DynamoDB |

Runs on the AWS free tier.

## Data sources
Open-Meteo Air Quality API (CAMS model data) and Historical Weather API.

## Limitations (honest notes)
- Pollution values are model-based estimates, not readings from ground sensors.
- Findings are correlations, not proven causes. The traffic finding is only an indicator.
- The surrounding-area comparison is coarse (tens of kilometres).
- Weather data lags by a few days, so very recent dates may show fewer findings.

## Future scope
Add ground-station data (OpenAQ/CPCB), real traffic and fire-count data, an AI-written narrative report via Amazon Bedrock, and alerts via SNS.

## Run / deploy
1. Create the DynamoDB table `air-cache` (partition key `cache_key`, String).
2. Create a Lambda (Python) from `backend/lambda_function.py` with a 30-second timeout and DynamoDB read/write permission.
3. Create an HTTP API with the route `GET /investigate` and enable CORS.
4. Put the API URL in `frontend/index.html` and deploy it on Amplify.
