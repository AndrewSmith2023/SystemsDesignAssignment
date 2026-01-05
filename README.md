# SystemsDesignAssignment

Cloud Based restaurant ordering system built with Flask and deployed on Google Cloud

# Tech Stack
- Python (Flask)
- Google App Engine
- Cloud SQL (MySQL)
- MongoDB Atlas
- Firebase Authentication
- Google Cloud functions

# Local development
1. Create virtual environment
2. Install dependencies
3. Set Environment Variables
4. Run python main.py

# Config and Secrets
- All secrets are stored in Google Secret Manager
- Environment Variables injected via app.yaml
- No credentials are commitetd to source control

# Deployment
Deploy to Google App Engine with gcloud app deploy