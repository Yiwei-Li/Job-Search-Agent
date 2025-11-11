from __future__ import annotations
import json, os, yaml
import certifi 
from dotenv import load_dotenv, set_key, get_key, find_dotenv
from pathlib import Path

os.environ["SSL_CERT_FILE"] = certifi.where()

import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup, SoupStrainer
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import re
import time
from datetime import datetime
import random
import pandas as pd
import numpy as np
from functools import wraps
from typing import Iterable, List
from openai import OpenAI
import subprocess

import smtplib, ssl, mimetypes
from smtplib import SMTPAuthenticationError
from email.message import EmailMessage


#==============================================================#


### helper functions ###
def human_wait(min_s=1, max_s=4):
    t = random.uniform(min_s, max_s)
    #print(f"Sleeping for {t} seconds")
    time.sleep(t)

class CredentialsError(RuntimeError):
    pass


def check_credentials():
    """
    Check if all required environment variables are set.
    """

    # Load .env file from root directory
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        raise CredentialsError("No .env file found in the root directory.")
    
    load_dotenv(dotenv_path, override=True)


    # check if chrome is installed and the version
    CHROME_PATH = Path(os.getenv("CHROME_PATH")).expanduser().resolve()
    if not CHROME_PATH.exists():
        raise CredentialsError(f"Chrome executable not found at misc folder. Please install Chrome for Testing app.")
    
    if not os.access(CHROME_PATH, os.X_OK):
        raise CredentialsError(f"Chrome executable at {CHROME_PATH} is not executable. Please check the file permissions.")
    
    result = subprocess.run([CHROME_PATH, "--version"], capture_output=True, text=True, check=True)
    chrome_version = result.stdout.strip().split()[-1].split(".")[0]


    # check if chrome version is recorded in .env
    # if not, set it
    CHROME_VERSION = os.getenv("CHROME_VERSION")
    if not CHROME_VERSION:
        set_key(dotenv_path, "CHROME_VERSION", chrome_version)


    # check if llm api key is set and valid 
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise CredentialsError("OpenAI API key not found in .env file.")
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        client.models.list()
    except Exception as e:
        raise CredentialsError("Invalid OpenAI API key.") from e

    
    # check if email credentials are set
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    if not SENDER_EMAIL or not RECIPIENT_EMAIL or not GMAIL_APP_PASSWORD:
        raise CredentialsError("Email credentials not found in .env file.")
    
    
    # check if user specified preferences and target roles
    CONFIG_PATH = os.getenv("CONFIG_PATH")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)   

    if not cfg["Preferences"] or not cfg["TargetRoles"]:
        raise CredentialsError("User preferences or target roles not specified in config file.")
    


def check_repost(text: str) -> bool:
    """
    Check if a job posting is a repost.
    """
    i = text.find("About the job")
    
    if i != -1:
        title = text[:i].lower()
        if "reposted" in title:
            return True
    return False


def crop_text(text: str, start_phrase: str, end_phrase: str) -> str:
    """
    Crop text between start_phrase and end_phrase.
    """
    
    i = text.find(start_phrase)
    j = text.find(end_phrase)

    if i > j and j != -1 and i != -1:
        return text.strip()

    if i == -1 and j == -1:
        return text.strip()
    elif i == -1 and j != -1:
        return text[:j].strip()
    elif i != -1 and j == -1:
        return text[i:].strip()
    elif i != -1 and j != -1:
        return text[i:j].strip()



### browser functions ###
def scroll_container_to_bottom(driver, el):
    # get total scrollable height
    scroll_height = driver.execute_script("return arguments[0].scrollHeight", el)
    #print(f"Total scrollable height: {scroll_height}")

    scroll_ct = 0
    total_scrolls = 0

    # the total scrollable height contains the ~1100px of the visible area, so we should remove it in the stop condition
    while total_scrolls + 1000 < scroll_height:
        step = random.randint(200, 500)
        driver.execute_script("arguments[0].scrollBy(0, arguments[1]);", el, step)
        #print(f"Scrolling by {step} pixels")
        total_scrolls += step
        scroll_ct += 1

        human_wait(0.75, 2.0)




### read/write file functions ###

def load_file(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read().strip() or "[]")
        return [str(x) for x in data]
    except FileNotFoundError:
        return []
    except Exception:
        return []


def write_file(ids: Iterable[str], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([str(x) for x in ids], f, separators=(",", ":"))



### SMTP function ###

def send_gmail(sender: str, receiver: str, attachment=None, filename="data.csv"):
    """
    Send a simple email via Gmail using an App Password.
    - sender: your Gmail address
    - app_password: your 16-char Gmail App Password (with 2FA enabled)
    - receiver: recipient email
    - attachment: optional; either a file path (str) or a pandas.DataFrame (sent as CSV)
    """

    msg = EmailMessage()
    msg["Subject"] = "Job Parse Results"
    main = f"""Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Total parsed jobs: {attachment.shape[0] if isinstance(attachment, pd.DataFrame) else 'N/A'}
Filtered for you: {(attachment['isFit'] == 'yes').sum() if isinstance(attachment, pd.DataFrame) else 'N/A'}
    """
    msg.set_content(main)

    msg["From"] = sender
    msg["To"] = receiver

    # Optional attachment
    if attachment is not None:
        try:
            # If it's a DataFrame, send as CSV in-memory
            if isinstance(attachment, pd.DataFrame):
                csv_bytes = attachment.to_csv(index=False).encode("utf-8")
                msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename=filename)
        except Exception as e:
            raise RuntimeError(f"Failed to add attachment: {e}")

    # Send via Gmail (587 + STARTTLS)
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    context = ssl.create_default_context()

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(sender, app_password)
        smtp.send_message(msg)





### LLM functions ###

PRICING = {
    "gpt-4.1-mini": {"per_1m_input": 0.4, "per_1m_output": 1.6},
    "gpt-5-mini": {"per_1m_input": 0.25, "per_1m_output": 2.0},
}

def estimate_cost(model: str, usage):
    if usage is None:
        return -99.0
    
    pricing = PRICING.get(model)

    if pricing is None:
        return -99.0
    
    input_tokens = getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", 0)
    total_tokens = getattr(usage, "total_tokens", 0)

    input_cost = (input_tokens / 1_000_000) * pricing["per_1m_input"]
    output_cost = (output_tokens / 1_000_000) * pricing["per_1m_output"]
    total_cost = input_cost + output_cost

    return total_cost
    



def pre_screen(listing: List[List[str]]):
    """
    Pre-screen job listings via LLM to extract fields and filter by relevance.
    """

    print("Pre-screening", len(listing), "job listings via LLM...")
    
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = "gpt-4.1-mini"

    # get user's target roles
    CONFIG_PATH = os.getenv("CONFIG_PATH")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    TARGET_ROLES = ", ".join(cfg['TargetRoles'])
    
    SYSTEM_PROMPT = """You extract fields from job listings and decide relevance. Use only the provided text. Output valid JSON only, no extra keys, comments, or prose.

# Rules
- Use ONLY the provided data.
- Do not add or infer information beyond the text.
- Return EXACTLY the JSON schema the workflow enforces. 

# Step 1. field extraction
- `positionTitle`: use the official title as written. If multiple appear, choose the shortest official title.
- `employerName`: use the employer name as written.
- `location`: use only the geographic part from the location information as written, exclude any unrelated context or trailing phrases that denote work mode.
- `salary`: use the exact salary information if explicitly mentioned, exclude any unrelated context or trailing phrases that denote benefits, else null.
- `remote`: only "Hybrid", "Remote", "On-site", or null if not mentioned.

# Step 2. relevance decision
- If the position is obviously unrelated to user's search terms, set `pass` = "no".
- Otherwise, set `pass` = "yes"."""

    USER_PROMPT = f"""Only use the data below. There are {len(listing)} job listings. Follow the System prompt exactly and return the required JSON for all {len(listing)} entries.

# User's search terms: {TARGET_ROLES}

# Job Listings:
{listing}
    """

    ITEM_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["positionTitle","pass","employerName","location","salary","remote"],
        "properties": {
            "positionTitle": {"type": "string"},
            "employerName": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"],
                         "pattern": r"^(?!.*\b(Remote|Hybrid|On-?site)\b).*$"},
            "pass": {"type": "string", "enum": ["yes", "no"]},
            "salary": {"type": ["string", "null"]},
            "remote": {"type": ["string", "null"], "enum": ["Hybrid", "Remote", "On-site", None]}
        }
    }
    
    SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["listings"], # <-- Ensure the model creates this key
        "properties": {
            "listings": { 
                "type": "array",
                "items": ITEM_SCHEMA # <-- Reference the item schema from above
            }
        }
    }

    resp = client.responses.create(
        model=model,
        temperature=0.1,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "JobListingPreScreen",
                "schema": SCHEMA,
                "strict": True
            }
        },
        max_output_tokens=3000
    )

    output = resp.output_text
    usage = getattr(resp, "usage", {})

    return output, usage, model




def jd_filter(jd, positionTitle, employerName):
    """
    Extract info from job description.
    Filter job description based on user's preferences. 
    """

    print("\tParsing", positionTitle, "at", employerName, "via LLM...")
    
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = "gpt-5-mini"

    # get user's preferences
    CONFIG_PATH = os.getenv("CONFIG_PATH")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    prefList = [f"- {str(s).strip()}" for s in cfg["Preferences"] if str(s).strip()]
    PREFERENCES = "\n".join(prefList)


    SYSTEM_PROMPT = """You are an experienced recruiter. Use only the data provided in the user prompt. Do not infer, guess, or use outside knowledge. Output only what the schema requires.

# Task
1. Extract core technical skills from the job description.
2. Decide fit using the rules below in order; stop at the first rule that applies.

# Skill extraction
- `technicalSkills`: 
  - Output an array of unique, concrete hard skills explicitly named in the job description (e.g., ["Python", "R", "SQL"]). Include everything you can find, from requirements and preferred/plus/nice to have sections.
  - Exclude soft skills, domains/subjects, and responsibilities (e.g., "communication", "epidemiology", "stakeholder management").
  - Use canonical names (e.g., "Excel", not "Microsoft Excel (advanced)").
  - If none found, return an empty array [].

# Fit decision (ONLY use these rules)
- If the role is not a full-time position: set `isFit` = "no" and `reason` = "NOT_FULLTIME"
- If the role explicitly states that no visa sponsorship is available: set `isFit` = "no" and `reason` = "NO_SPONSORSHIP"
- If the role explicitly states that require US citizenship or require secret clearance: set `isFit` = "no" and `reason` = "US_CITIZEN_ONLY"
- If the role requires PhD degree: set `isFit` = "no" and `reason` = "PHD_REQUIRED".
- If the role opens to internal applicants only: set `isFit` = "no" and `reason` = "INTERNAL_ONLY".
- If the role requires a minimum years of work experience longer than 4 years and cannot subsitute using 2 years of graduate degree: set `isFit` = "no" and `reason` = "YEAR_EXCEED_MIN - " + a brief reason.
- If the role does not match the user's preferences: `isFit` = "no" and `reason` = "PREFERENCE_VIOLATE - " + a brief reason.

If multiple rejection criteria are met, only give the first rejection reason you used. Keep the reason succinct. Otherwise, set `isFit` = "yes" and `reason` = null."""


    USER_PROMPT = f"""Only use the data I provide below. Extract technical skills from the job description. Apply the Fit decision rules from the System Prompt in order. Do not infer or assume anything beyond what is explicitly stated in the job description. Return only the JSON required by the workflow schema.

# User Preferences: 
{PREFERENCES}

# positionTitle: {positionTitle}

# Job Description:
{jd}"""

    ITEM_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["technicalSkills", "isFit", "reason"],
        "properties": {
            "technicalSkills": {
            "type": "array",
            "minItems": 0,
            "items": {"type": "string", "minLength": 1}
            },
            "isFit": {"type": "string", "enum": ["yes", "no"]},
            "reason": {"type": ["string", "null"]}
        }
    }
    
    SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision"], 
        "properties": {
            "decision": { 
                "type": "array",
                "items": ITEM_SCHEMA
            }
        }
    }

    resp = client.responses.create(
        model=model,
        reasoning={"effort": "low"},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "JobListingPreScreen",
                "schema": SCHEMA,
                "strict": True
            }
        },
        max_output_tokens=1500
    )

    output = resp.output_text
    usage = getattr(resp, "usage", {})

    return output, usage, model








#==============================================================#


check_credentials()


### define paths ###
CHROME_PATH = Path(os.getenv("CHROME_PATH")).expanduser().resolve()
PROFILE_DATA_DIR = Path(os.getenv("PROFILE_DATA_DIR")).expanduser().resolve()
SEARCH_URL = str(os.getenv("SEARCH_URL")) or ""
SEEN_PATH = Path(os.getenv("SEEN_PATH")).expanduser().resolve()
BLOCKLIST_PATH = Path(os.getenv("BLOCKLIST_PATH")).expanduser().resolve()


# check if profile data directory exists
# if not, prompt user to manually login 
has_profile = PROFILE_DATA_DIR.is_dir()







# set up undetected-chromedriver with custom profile
options = Options()
options.binary_location = str(CHROME_PATH)                 # point to CfT (arm64)
options.add_argument(f"--user-data-dir={str(PROFILE_DATA_DIR)}") 
options.add_argument("--profile-directory=jobagent-profile")
#options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


driver = uc.Chrome(options=options, version_main=int(os.getenv("CHROME_VERSION")))                  
driver.set_window_size(1300,1000)


# fire up browser to LinkedIn job search page
if not has_profile or not SEARCH_URL:
    driver.get("https://www.linkedin.com/login")
    print("Please log in to LinkedIn in the opened browser window and search for the job title you are interested in. Remember to set up all filters (e.g., location, date posted, experience level, etc.) before proceeding.")
    
    time.sleep(3)

    input("Make sure this is the search results you want to monitor! Press Enter to continue...")

    # save the current URL as SEARCH_URL
    targetUrl = driver.current_url
    parsed = urlsplit(targetUrl)
    keysRemove = ["currentJobId", "origin"]
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keep = [(k,v) for k,v in pairs if k not in keysRemove]
    cleanedUrl = urlunsplit(parsed._replace(query=urlencode(keep, doseq=True)))

    set_key(dotenv_path, "SEARCH_URL", cleanedUrl)


else:

    driver.get(SEARCH_URL)

    print("Make sure this is the search results you want to monitor! You can change it in the .env file if needed.")




##############################
### interact with the page ###
##############################

# find the scrollable container
results = driver.execute_script("""
const cands = Array.from(document.querySelectorAll('div,section,ul')).filter(el => {
  const s = getComputedStyle(el);
  return (s.overflowY === 'auto' || s.overflowY === 'scroll') &&
         el.scrollHeight > el.clientHeight && el.clientHeight > 200;
});
return cands[0] || document.scrollingElement;
""")

# highlight the scrollable container for debugging
driver.execute_script("""
arguments[0].style.outline='3px solid darkorange';
arguments[0].style.outlineOffset='-2px';
""", results)

# driver.execute_script("""
# arguments[0].style.removeProperty('outline');
# arguments[0].style.removeProperty('outline-offset');
# """, results)


human_wait(2,4)
scroll_container_to_bottom(driver, results)
human_wait(1.5, 3.5)

# wait for page load
WebDriverWait(driver, 60, poll_frequency=2).until(lambda d: d.execute_script("return document.readyState") == "complete")

# get page source 
html = driver.page_source


##########################
### parse job listings ###
##########################

seenIDs = set(load_file(str(SEEN_PATH)))
blocklist = set(load_file(str(BLOCKLIST_PATH)))

soup = BeautifulSoup(html, "html.parser")
ul = soup.select_one("div.authentication-outlet main#main ul")
lis = ul.find_all("li", recursive=False)

listings = []
urls = []
jobids = set() # track all jobids seen this run
seenJobids = [] # track jobids that have been seen before
invalidCt = 0 # track invalid jobids counts


# iterate through job listings
for i, li in enumerate(lis, 1):
    jobid = str(li.get("data-occludable-job-id")) or ""

    # skip invalid jobids
    if not jobid:
        invalidCt += 1
        continue

    # skip seen jobids
    if jobid in seenIDs:
        temp = {}
        temp['addDate'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        temp['descriptionURL'] = f"https://www.linkedin.com/jobs/view/{jobid}/"
        temp['isFit'] = "skip"
        temp['reason'] = "SEEN_JOB"
        seenJobids.append(temp)

        jobids.add(jobid)
        continue
    
    # for all new valid jobids
    jobids.add(jobid)
    # get listing info text from span tags
    spanTexts = [s.get_text(strip=True) for s in li.find_all("span")]
    spanTexts = [s for s in spanTexts if s.strip()]

    listings.append(spanTexts)
    urls.append(f"https://www.linkedin.com/jobs/view/{jobid}/")

# print stats
print(f"{len(lis)} total job listings found on the page.")
if invalidCt > 0:
    print(f"{invalidCt} listings skipped due to missing job IDs.")
if seenJobids:
    print(f"{len(seenJobids)} listings skipped as they have been seen in the previous run.")
print(f"{len(listings)} new job listings to be processed.")

# update seen IDs file
write_file(jobids, str(SEEN_PATH))



#################################
### Job listing pre-screening ###
#################################

output, usage, model = pre_screen(listings)
pscost = estimate_cost(model, usage)
print(f"Estimated cost for job pre-screen call: ${pscost:.6f}")

# filter out blocklisted employers
output = json.loads(output)

preScreenList = []
outJobids = [] # track jobids that are blocklisted or pre-screen filtered out

for idx, job in enumerate(output['listings']):
    if job['employerName'] in blocklist:
        # pop `pass` key
        job.pop('pass', None)
 
        job['descriptionURL'] = urls[idx]
        job['addDate'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job['isFit'] = "skip"
        job['reason'] = "BLOCKLISTED_EMPLOYER"
        outJobids.append(job)

        continue

    if job['pass'] == "no":
        # pop `pass` key
        job.pop('pass', None)
 
        job['descriptionURL'] = urls[idx]
        job['addDate'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        job['isFit'] = "skip"
        job['reason'] = "PRE_SCREEN_FILTERED_OUT"
        outJobids.append(job)

        continue

    # pop `pass` key
    job.pop('pass', None)
    # add descriptionURL
    job['descriptionURL'] = urls[idx]
    preScreenList.append(job)

human_wait(2,5)



##############################
### Job Description Filter ###
##############################

print(f"Reading through {len(preScreenList)} pre-screened job descriptions.")

main_handle = driver.current_window_handle
finalList = preScreenList.copy()
totalCost = 0.0

for idx, job in enumerate(preScreenList):
    
    # fetch job description page
    driver.switch_to.new_window('tab')
    driver.get(job['descriptionURL'])

    # wait for page load
    WebDriverWait(driver, 60, poll_frequency=2).until(lambda d: d.execute_script("return document.readyState") == "complete")

    human_wait(2,5)
    
    # get page source
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    maintag = soup.find("main")
    jdAll = maintag.get_text(separator="\n", strip=True)

    # skip reposted jobs
    if check_repost(jdAll):
        #finalList[idx]['skills'] = str(output['decision'][0]['technicalSkills'])
        finalList[idx]['isFit'] = "skip"
        finalList[idx]['reason'] = "REPOSTED_JOB"
        finalList[idx]['addDate'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        driver.close()
        driver.switch_to.window(main_handle)

        human_wait(10,20)
        continue


    # crop to main jd content
    jdMain = crop_text(jdAll, "About the job", "\nSee more\nSet alert for similar jobs\n")

    # filter via LLM
    output, usage, model = jd_filter(jdMain, job['positionTitle'], job['employerName'])
    totalCost += estimate_cost(model, usage)

    output = json.loads(output)

    # add results to final list for new/repost jobs
    finalList[idx]['skills'] = str(output['decision'][0]['technicalSkills'])
    finalList[idx]['isFit'] = output['decision'][0]['isFit']
    finalList[idx]['reason'] = output['decision'][0]['reason']
    finalList[idx]['addDate'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    driver.close()
    driver.switch_to.window(main_handle)

    human_wait(2,5)

driver.quit()

print(f"Estimated total cost for job description filter calls: ${totalCost:.6f}")
print(f"Estimated total cost for the entire run: ${pscost + totalCost:.6f}")




#####################
### Final Results ###
#####################

screenedJobs = pd.DataFrame(finalList)
seenJobs = pd.DataFrame(seenJobids)
outJobs = pd.DataFrame(outJobids)

df = pd.concat([screenedJobs, seenJobs, outJobs], ignore_index=True)
df = df.sort_values(by="addDate", ascending=True).reset_index(drop=True)

order = ["addDate", "employerName", "positionTitle", "location", "salary", "remote", "skills", "descriptionURL", "isFit", "reason"]
df = df.loc[:, order]


Path("./results").mkdir(parents=True, exist_ok=True)

ts = datetime.now().strftime('%Y%m%d_%H%M')
df.to_csv(f"./results/{str(ts)}_result.csv", index=False)

send_gmail(sender=os.getenv("SENDER_EMAIL"), receiver=os.getenv("RECIPIENT_EMAIL"), attachment=df, filename=f"{str(ts)}_result.csv")