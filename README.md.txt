# Boxing Calendar Scraper

Python + undetected-chromedriver scraper for Boxing247 that:

- Bypasses bot protection
- Extracts fight schedule as text
- Converts times to Central Time using real time zones
- Generates `boxing_schedule.ics` with:
  - Individual fight events
  - Location
  - TV network
  - Full fight-night card in each description

## Usage

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install undetected-chromedriver ics


SOURCE: https://www.boxing247.com/fight-schedule