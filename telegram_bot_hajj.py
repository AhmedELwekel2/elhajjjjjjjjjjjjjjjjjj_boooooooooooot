import requests
import json
from datetime import datetime, timedelta
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
import os
import tempfile
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import re
import time
from newspaper import Article
import nltk
from readability import readability
import feedparser
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from jinja2 import Environment, FileSystemLoader
import boto3
from botocore.config import Config
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except OSError:
    WEASYPRINT_AVAILABLE = False
    logging.warning("WeasyPrint (GTK) not found. PDF generation will be disabled.")
except ImportError:
    WEASYPRINT_AVAILABLE = False
    logging.warning("WeasyPrint module not found.")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Download required NLTK data (run once)
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

# Register Arabic font
try:
    pdfmetrics.registerFont(TTFont('Amiri', 'Amiri-Regular.ttf'))
except Exception as e:
    logger.error(f"Failed to register Arabic font: {e}")

# Usage limits configuration
USAGE_LIMITS = {
    'daily_news': 30,
    'weekly': 4,
    'monthly': 2,
    'magazine': 2
}

# Admin user IDs (add your Telegram user ID here)
ADMIN_USER_IDS = [1029062753]  # Add admin IDs like [123456789, 987654321]

# Usage tracking file
USAGE_FILE = 'user_usage.json'

def load_usage_data():
    """Load usage data from JSON file."""
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading usage data: {e}")
            return {}
    return {}

def save_usage_data(usage_data):
    """Save usage data to JSON file."""
    try:
        with open(USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(usage_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving usage data: {e}")

def get_user_id(update):
    """Get user ID from update."""
    if update.callback_query:
        return update.callback_query.from_user.id
    return update.message.from_user.id

def check_usage_limit(user_id, feature):
    """Check if user has reached usage limit for a feature."""
    usage_data = load_usage_data()
    user_key = str(user_id)
    
    if user_key not in usage_data:
        return True, 0  # New user, has limit
    
    user_usage = usage_data[user_key]
    feature_key = feature
    
    if feature_key not in user_usage:
        return True, 0  # Feature not used yet
    
    current_usage = user_usage[feature_key]
    limit = USAGE_LIMITS.get(feature, 0)
    
    if current_usage >= limit:
        return False, current_usage  # Limit reached
    return True, current_usage  # Still has usage left

def increment_usage(user_id, feature):
    """Increment usage count for a user and feature."""
    usage_data = load_usage_data()
    user_key = str(user_id)
    
    if user_key not in usage_data:
        usage_data[user_key] = {}
    
    if feature not in usage_data[user_key]:
        usage_data[user_key][feature] = 0
    
    usage_data[user_key][feature] += 1
    save_usage_data(usage_data)

def reset_user_usage(user_id=None):
    """Reset usage for a specific user or all users."""
    if user_id:
        usage_data = load_usage_data()
        user_key = str(user_id)
        if user_key in usage_data:
            usage_data[user_key] = {}
            save_usage_data(usage_data)
            return True
        return False
    else:
        # Reset all users
        save_usage_data({})
        return True

def get_usage_status(user_id):
    """Get current usage status for a user."""
    usage_data = load_usage_data()
    user_key = str(user_id)
    
    if user_key not in usage_data:
        return {
            'daily_news': {'used': 0, 'limit': USAGE_LIMITS['daily_news']},
            'weekly': {'used': 0, 'limit': USAGE_LIMITS['weekly']},
            'monthly': {'used': 0, 'limit': USAGE_LIMITS['monthly']},
            'magazine': {'used': 0, 'limit': USAGE_LIMITS['magazine']}
        }
    
    user_usage = usage_data[user_key]
    return {
        'daily_news': {'used': user_usage.get('daily_news', 0), 'limit': USAGE_LIMITS['daily_news']},
        'weekly': {'used': user_usage.get('weekly', 0), 'limit': USAGE_LIMITS['weekly']},
        'monthly': {'used': user_usage.get('monthly', 0), 'limit': USAGE_LIMITS['monthly']},
        'magazine': {'used': user_usage.get('magazine', 0), 'limit': USAGE_LIMITS['magazine']}
    }

# Your Telegram Bot Token (you'll need to get this from @BotFather)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8421984697:AAEt4JJ8wohOYGkCEEOjAGtohykgIlXCUzU")

# AWS Bedrock Configuration
# Support both variable name formats for flexibility
AWS_BEARER_TOKEN_BEDROCK = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_BEDROCK_REGION", "us-east-1")
AWS_BEDROCK_INFERENCE_PROFILE = os.getenv("AWS_BEDROCK_INFERENCE_PROFILE") or os.getenv("AWS_BEDROCK_INFERENCE_PROFILE_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# Initialize AWS Bedrock client
try:
    if not AWS_BEARER_TOKEN_BEDROCK:
        logger.warning("⚠️ AWS_BEARER_TOKEN_BEDROCK environment variable is not set!")
        logger.warning("   Please set it using:")
        logger.warning("   Windows: set AWS_BEARER_TOKEN_BEDROCK=your_token")
        logger.warning("   Linux/Mac: export AWS_BEARER_TOKEN_BEDROCK=your_token")
        raise ValueError("AWS_BEARER_TOKEN_BEDROCK environment variable is required")
    
    # Standard client with default timeout (60 seconds)
    bedrock_client = boto3.client(
        service_name="bedrock-runtime",
        region_name=AWS_REGION
    )
    
    # Long-running client for magazine generation (600 seconds timeout)
    bedrock_config_long = Config(
        read_timeout=600,
        connect_timeout=10,
        retries={'max_attempts': 1}
    )
    bedrock_client_long = boto3.client(
        service_name="bedrock-runtime",
        region_name=AWS_REGION,
        config=bedrock_config_long
    )
    
    logger.info(f"✅ AWS Bedrock client initialized successfully")
    logger.info(f"   Region: {AWS_REGION}")
    logger.info(f"   Inference Profile: {AWS_BEDROCK_INFERENCE_PROFILE}")
    logger.info(f"   Standard timeout: 60s, Long operations timeout: 600s")
except Exception as e:
    logger.error(f"❌ Failed to initialize AWS Bedrock client: {str(e)}")
    raise

# No RSS feeds needed - using haj.gov.sa API and CNN Arabic scraping
RSS_FEEDS = []

KEYWORD_INPUT_INSTRUCTIONS = (
    "✍️ *إعداد الكلمات المفتاحية (بالإنجليزية)*\n"
    "أرسل الكلمات المفتاحية بالصيغة التالية (بالإنجليزية):\n"
    "`Primary Keyword | secondary keyword 1, secondary keyword 2, secondary keyword 3`\n\n"
    "مثال:\n"
    "`Hajj News 2026 | hajj, umrah, pilgrimage, makkah`\n\n"
    "أرسل كلمة *cancel* لإلغاء إدخال الكلمات المفتاحية."
)


def parse_keyword_input(raw_text):
    if not raw_text:
        return None
    parts = raw_text.split('|', 1)
    primary = parts[0].strip()
    if not primary:
        return None
    secondary = []
    if len(parts) > 1:
        secondary = [kw.strip() for kw in parts[1].split(',') if kw.strip()]
    return {"primary": primary, "secondary": secondary}


def format_secondary_keywords(secondary_list):
    if not secondary_list:
        return "لم يتم تحديد كلمات ثانوية"
    return ", ".join(secondary_list)


def build_keyword_instruction_block(keywords):
    if keywords and keywords.get("primary"):
        primary = keywords["primary"]
        secondary_text = format_secondary_keywords(keywords.get("secondary", []))
        keyword_header = (
            f'PRIMARY KEYWORD: "{primary}"\n'
            f"SECONDARY KEYWORDS / LSI: {secondary_text}\n"
        )
    else:
        keyword_header = (
            "PRIMARY KEYWORD: Not specified (infer the best fit from the Hajj and Umrah coverage)\n"
            "SECONDARY KEYWORDS / LSI: Use related Hajj, Umrah, pilgrimage terms, synonyms, and supporting subtopics\n"
        )

    return f"""
{keyword_header}
SEO requirements:
- Place the primary keyword in:
  • The SEO Title
  • The H1
  • The first paragraph (within the first 100 words)
  • Naturally 2–3 times every ~300 words throughout the body
- Distribute secondary/LSI keywords across select H2/H3 headings and different paragraphs as thematic synonyms.
- Do NOT repeat the exact same keyword in every heading—use natural variations to avoid keyword stuffing.

Mandatory SEO outputs at the top of the response (before any other sections):
1. SEO Title: < 60 characters, includes the primary keyword and communicates a clear benefit.
2. Meta Description: 120–150 characters summarizing the main value, optionally includes the primary keyword once (only if it reads naturally) plus a light CTA.
3. Recommended Slug: lowercase, hyphen-separated version of the primary keyword (e.g., hajj-news-2026).
4. Headings Structure: Proposed H2/H3 outline derived from the primary + secondary keywords using varied phrasing.

After listing these SEO elements, continue with the requested Hajj news blog structure while following the keyword guidance above.
""".strip()


def keywords_summary_text(keywords):
    if not keywords or not keywords.get("primary"):
        return "لم يتم إعداد أي كلمات مفتاحية بعد."
    secondary = format_secondary_keywords(keywords.get("secondary", []))
    return f"الكلمة الأساسية: {keywords['primary']}\nالكلمات الثانوية: {secondary}"


def get_user_keywords(context):
    try:
        return context.user_data.get("blog_keywords")
    except Exception:
        return None

def fetch_hajgov_news(page_size=50):
    """Fetch Hajj news from haj.gov.sa REST API"""
    url = "https://haj.gov.sa/s-core/customApi/News/GetAll"
    params = {
        'PageSize': page_size,
        'PageNumber': 1,
        'Language': 'ar'
    }
    articles = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        items = data if isinstance(data, list) else data.get('data', data.get('items', data.get('result', [])))
        if isinstance(items, dict):
            items = items.get('items', items.get('data', []))
        
        for item in items:
            try:
                title = item.get('title', '')
                publish_date = item.get('publishDate', '')
                
                # Extract description from nested value
                desc_obj = item.get('description', {})
                description = ''
                if isinstance(desc_obj, dict):
                    description = desc_obj.get('value', '')
                elif isinstance(desc_obj, str):
                    description = desc_obj
                
                # Strip HTML tags from description
                if description:
                    description = re.sub(r'<[^>]+>', '', description).strip()
                
                # Build article URL
                item_path = item.get('itemPath', '')
                article_url = f"https://haj.gov.sa{item_path}" if item_path else ''
                
                # Get image
                image_item = item.get('imageItem', {})
                image_url = ''
                if isinstance(image_item, dict):
                    image_url = image_item.get('src', '')
                
                # Parse date
                published_iso = ''
                if publish_date:
                    try:
                        if 'T' in publish_date:
                            published_iso = publish_date
                        else:
                            dt = datetime.strptime(publish_date, '%Y-%m-%d')
                            published_iso = dt.isoformat()
                    except:
                        published_iso = publish_date
                
                article = {
                    'title': title,
                    'description': description[:500] if description else '',
                    'full_content': description,  # haj.gov.sa provides full text
                    'url': article_url,
                    'publishedAt': published_iso,
                    'source': {'name': 'وزارة الحج والعمرة'},
                    'image_url': image_url,
                    'extraction_method': 'api',
                    'content_length': len(description) if description else 0
                }
                articles.append(article)
            except Exception as e:
                logger.warning(f"Error parsing haj.gov.sa article: {e}")
                continue
        
        logger.info(f"Fetched {len(articles)} articles from haj.gov.sa")
        return articles
    
    except Exception as e:
        logger.error(f"Error fetching haj.gov.sa news: {e}")
        return []

def fetch_cnn_hajj_news():
    """Scrape Hajj news from CNN Arabic tag page"""
    url = "https://arabic.cnn.com/tag/alhj"
    articles = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ar,en;q=0.9'
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Find article links - CNN Arabic uses various card structures
        article_links = soup.select('a[href*="/article/"]')
        
        seen_urls = set()
        for link_el in article_links:
            try:
                href = link_el.get('href', '')
                if not href or href in seen_urls:
                    continue
                
                # Build full URL
                if href.startswith('/'):
                    full_url = f"https://arabic.cnn.com{href}"
                elif href.startswith('http'):
                    full_url = href
                else:
                    continue
                
                seen_urls.add(href)
                
                # Extract title from the link text or nested elements
                title = ''
                title_el = link_el.select_one('span, h2, h3, .cd__headline-text')
                if title_el:
                    title = title_el.get_text(strip=True)
                if not title:
                    title = link_el.get_text(strip=True)
                
                if not title or len(title) < 10:
                    continue
                
                # Try to extract date from URL pattern (YYYY/MM/DD)
                published_iso = ''
                date_match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', full_url)
                if date_match:
                    try:
                        dt = datetime(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
                        published_iso = dt.isoformat()
                    except:
                        pass
                
                article = {
                    'title': title,
                    'description': '',
                    'url': full_url,
                    'publishedAt': published_iso,
                    'source': {'name': 'CNN عربية'}
                }
                articles.append(article)
            except Exception as e:
                logger.warning(f"Error parsing CNN Arabic article: {e}")
                continue
        
        logger.info(f"Fetched {len(articles)} articles from CNN Arabic")
        return articles
    
    except Exception as e:
        logger.error(f"Error fetching CNN Arabic Hajj news: {e}")
        return []

def filter_recent_articles(articles, days=7):
    """Filter articles to only include those from the past specified days, and strictly not older than 2026"""
    if not articles:
        return []
    
    cutoff_date = datetime.now() - timedelta(days=days)
    
    # Enforce minimum date to be January 1, 2026 for the 2026 Hajj season
    min_date = datetime(2026, 1, 1)
    if cutoff_date < min_date:
        cutoff_date = min_date
        
    recent_articles = []
    
    for article in articles:
        if not article:
            continue
        published_at = article.get('publishedAt') or article.get('published_at')
        
        # Check URL or title for old years just to be extra safe
        url = article.get('url', '')
        title = article.get('title', '')
        if re.search(r'/(2025|2024|2023|2022|2021)/', url) or re.search(r'\b(2025|2024|2023|2022|2021)\b', title):
            continue
            
        if published_at:
            try:
                # Handle different date formats
                if 'T' in published_at:
                    pub_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                else:
                    # Truncate to first 10 chars for robust date parsing (YYYY-MM-DD)
                    pub_date = datetime.strptime(published_at[:10], '%Y-%m-%d')
                
                if pub_date.replace(tzinfo=None) >= cutoff_date:
                    recent_articles.append(article)
            except Exception as e:
                # If date parsing fails, check if string contains old years
                pub_str = str(published_at)
                if any(old_year in pub_str for old_year in ['2025', '2024', '2023', '2022', '2021']):
                    continue
                # If it doesn't clearly contain an old year, include it
                recent_articles.append(article)
        else:
            # If no date available, include the article (already checked URL/title for old years above)
            recent_articles.append(article)
    
    return recent_articles

def categorize_articles(articles):
    """Categorize articles by Hajj and Umrah topics"""
    if not articles:
        logger.warning("No articles provided to categorize_articles")
        return {
            'خدمات الحجاج': [],
            'التنظيم والإدارة': [],
            'التقنية والابتكار': [],
            'الصحة والسلامة': [],
            'أخبار عامة': []
        }
    
    categories = {
        'خدمات الحجاج': [],
        'التنظيم والإدارة': [],
        'التقنية والابتكار': [],
        'الصحة والسلامة': [],
        'أخبار عامة': []
    }
    
    services_keywords = ['خدمات', 'حجاج', 'معتمرين', 'تفويج', 'نقل', 'إسكان', 'سكن', 'إعاشة', 'تغذية', 'مخيمات', 'حملات', 'تصاريح', 'تأشيرات', 'نسك']
    org_keywords = ['تنظيم', 'إدارة', 'وزارة', 'هيئة', 'إشراف', 'خطة', 'استعداد', 'تشغيل', 'موسم', 'منظومة', 'طاقة استيعابية', 'أعداد']
    tech_keywords = ['تقنية', 'تطبيق', 'رقمي', 'ذكاء اصطناعي', 'إلكتروني', 'منصة', 'روبوت', 'ابتكار', 'تحول رقمي', 'smart', 'digital']
    health_keywords = ['صحة', 'سلامة', 'طبي', 'مستشفى', 'إسعاف', 'وقاية', 'حرارة', 'ضربة شمس', 'وباء', 'تطعيم', 'لقاح', 'إنقاذ', 'أمن']
    
    for article in articles:
        if not article:
            continue
        title = article.get('title', '') or ''
        description = article.get('description', '') or ''
        full_content = article.get('full_content', '') or ''
        content = f"{title} {description} {full_content[:500]}"
        
        if any(keyword in content for keyword in services_keywords):
            categories['خدمات الحجاج'].append(article)
        elif any(keyword in content for keyword in org_keywords):
            categories['التنظيم والإدارة'].append(article)
        elif any(keyword in content for keyword in tech_keywords):
            categories['التقنية والابتكار'].append(article)
        elif any(keyword in content for keyword in health_keywords):
            categories['الصحة والسلامة'].append(article)
        else:
            categories['أخبار عامة'].append(article)
    
    return categories

def extract_article_content(url, max_retries=3):
    """Extract full article content from URL using multiple methods"""
    if not url or url.strip() == '':
        return None
    
    content = None

    # Special handling for NIST (National Institute of Standards and Technology)
    if 'nist.gov' in url:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Selector for NIST full text
            content_div = soup.select_one('.text-with-summary')
            if content_div:
                # Cleanup
                for element in content_div(['script', 'style']):
                    element.decompose()
                
                text = content_div.get_text(strip=True, separator=' ')
                if len(text) > 200:
                    return {
                        'text': text,
                        'title': soup.find('title').get_text(strip=True) if soup.find('title') else '',
                        'method': 'nist_custom',
                        'publish_date': None
                    }
        except Exception as e:
            logger.warning(f"NIST custom extraction failed for {url}: {e}")
            # Fallthrough to standard methods

    # Special handling for EOS (Egyptian Organization for Standardization)
    if 'eos.org.eg' in url:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # EOS specific selector for body
            content_div = soup.select_one('div.text-custom-text-1')
            if content_div:
                # Remove scripts/styles if any
                for element in content_div(['script', 'style']):
                    element.decompose()
                
                text = content_div.get_text(strip=True, separator=' ')
                if len(text) > 100:
                     return {
                        'text': text,
                        'title': soup.find('title').get_text(strip=True) if soup.find('title') else '',
                        'method': 'eos_custom',
                        'publish_date': None
                    }
        except Exception as e:
            logger.warning(f"EOS custom extraction failed for {url}: {e}")
            # Fallthrough to standard methods

    # Special handling for EGAC
    if 'egac.gov.eg' in url:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Selector for EGAC full text
            content_div = soup.select_one('.new-details-container .text')
            if content_div:
                for element in content_div(['script', 'style']):
                    element.decompose()
                text = content_div.get_text(strip=True, separator=' ')
                if len(text) > 100:
                    return {
                        'text': text,
                        'title': soup.find('title').get_text(strip=True) if soup.find('title') else '',
                        'method': 'egac_custom',
                        'publish_date': None
                    }
        except Exception as e:
            logger.warning(f"EGAC custom extraction failed for {url}: {e}")

    # Method 1: Try newspaper3k first (most reliable for news articles)
    try:
        article = Article(url)
        article.download()
        article.parse()
        
        if article.text and len(article.text.strip()) > 200:
            content = {
                'text': article.text.strip(),
                'title': article.title or '',
                'authors': article.authors or [],
                'publish_date': article.publish_date,
                'method': 'newspaper3k'
            }
            logger.info(f"Successfully extracted content using newspaper3k for {url}")
            return content
    except Exception as e:
        logger.warning(f"Newspaper3k failed for {url}: {str(e)}")
    
    # Method 2: Manual web scraping with BeautifulSoup
    for attempt in range(max_retries):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'ads']):
                element.decompose()
            
            # Try multiple content selectors
            content_selectors = [
                'article',
                '[role="main"]',
                '.article-content',
                '.post-content',
                '.entry-content',
                '.content',
                'main',
                '.story-body',
                '.article-body',
                '.post-body'
            ]
            
            article_text = ""
            for selector in content_selectors:
                elements = soup.select(selector)
                if elements:
                    for element in elements:
                        text = element.get_text(strip=True, separator=' ')
                        if len(text) > len(article_text):
                            article_text = text
                    break
            
            # Fallback: extract all paragraphs
            if not article_text or len(article_text) < 200:
                paragraphs = soup.find_all('p')
                article_text = ' '.join([p.get_text(strip=True) for p in paragraphs])
            
            # Clean up the text
            article_text = re.sub(r'\s+', ' ', article_text)
            article_text = article_text.strip()
            
            if article_text and len(article_text) > 200:
                content = {
                    'text': article_text,
                    'title': soup.find('title').get_text(strip=True) if soup.find('title') else '',
                    'method': 'beautifulsoup'
                }
                logger.info(f"Successfully extracted content using BeautifulSoup for {url}")
                return content
                
        except requests.RequestException as e:
            logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}) for {url}: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        except Exception as e:
            logger.error(f"Unexpected error extracting content from {url}: {str(e)}")
            break
    
    logger.error(f"Failed to extract content from {url} after all attempts")
    return None

def enhance_articles_with_content(articles, max_articles=15, weekly_mode=False, monthly_mode=False):
    """Enhance articles with full content extraction"""
    if not articles:
        logger.warning("No articles provided to enhance_articles_with_content")
        return []
    
    enhanced_articles = []
    
    # Adjust for weekly vs daily processing
    if weekly_mode:
        max_articles = min(max_articles, 50)
        delay = 0.3
    elif monthly_mode:
        max_articles = min(max_articles, 100)
        delay = 0.5
    else:
        max_articles = min(max_articles, 20)
        delay = 0.5
    
    logger.info(f"Starting content extraction for {min(len(articles), max_articles)} articles")
    
    for i, article in enumerate(articles[:max_articles]):
        try:
            url = article.get('url', '') if article else ''
            if not url:
                continue
                
            logger.info(f"Extracting content {i+1}/{min(len(articles), max_articles)}: {url}")
            
            # Extract full content
            content_data = extract_article_content(url)
            
            # Enhance article with extracted content
            enhanced_article = article.copy()
            if content_data and content_data.get('text'):
                enhanced_article['full_content'] = content_data['text']
                enhanced_article['extraction_method'] = content_data['method']
                enhanced_article['content_length'] = len(content_data['text'])
                
                # Use extracted title if original is missing/short
                extracted_title = content_data.get('title', '')
                original_title = article.get('title', '')
                if extracted_title and original_title and len(extracted_title) > len(original_title):
                    enhanced_article['enhanced_title'] = extracted_title
            else:
                description = article.get('description', 'No content available')
                enhanced_article['full_content'] = description or 'No content available'
                enhanced_article['extraction_method'] = 'fallback'
                enhanced_article['content_length'] = len(description) if description else 0
            
            enhanced_articles.append(enhanced_article)
            
            # Respectful delay
            time.sleep(delay)
            
        except Exception as e:
            logger.error(f"Error processing article {i+1}: {str(e)}")
            # Add original article without enhancement
            enhanced_articles.append(article)
            continue
    
    logger.info(f"Content extraction completed. Enhanced {len([a for a in enhanced_articles if a.get('full_content')])} articles")
    return enhanced_articles

async def get_news(update: Update, context: ContextTypes.DEFAULT_TYPE, page=1, category=None):
    """Get today's enhanced Hajj news with presenter-style summary."""
    user_id = get_user_id(update)
    
    # Check usage limit
    has_limit, current_usage = check_usage_limit(user_id, 'daily_news')
    if not has_limit:
        limit_message = (
            f"❌ *تم الوصول إلى الحد الأقصى*\n\n"
            f"لقد استخدمت جميع المحاولات المتاحة للأخبار اليومية ({USAGE_LIMITS['daily_news']}/{USAGE_LIMITS['daily_news']}).\n\n"
        )
        if update.callback_query:
            await update.callback_query.answer("تم الوصول إلى الحد الأقصى", show_alert=True)
            await update.callback_query.message.reply_text(limit_message, parse_mode='Markdown')
        else:
            await update.message.reply_text(limit_message, parse_mode='Markdown')
        return
    
    # Increment usage
    increment_usage(user_id, 'daily_news')
    
    # Send initial message
    if update.callback_query:
        await update.callback_query.answer()
        message = await update.callback_query.message.reply_text(
            "🕋 جارٍ تجهيز موجز أخبار الحج والعمرة...\n📖 يتم الآن جمع الأخبار من وزارة الحج و CNN عربية...\n⏳ يرجى الانتظار للحظات.",
            parse_mode='Markdown'
        )
    else:
        message = await update.message.reply_text(
            "🕋 جارٍ تجهيز موجز أخبار الحج والعمرة...\n📖 يتم الآن جمع الأخبار من وزارة الحج و CNN عربية...\n⏳ يرجى الانتظار للحظات.",
            parse_mode='Markdown'
        )
    
    try:
        # Update progress message
        await message.edit_text(
            "🌍 *الخطوة 1/3:* جلب الأخبار من المصادر...",
            parse_mode='Markdown'
        )
        
        # Fetch news from Hajj sources
        hajgov_articles = fetch_hajgov_news() or []
        cnn_articles = fetch_cnn_hajj_news() or []
        logger.info(f"Fetched {len(hajgov_articles)} haj.gov.sa, {len(cnn_articles)} CNN Arabic")
        
        # Daily scope: restrict to past 7 days
        recent_hajgov = filter_recent_articles(hajgov_articles, days=7) or []
        recent_cnn = filter_recent_articles(cnn_articles, days=7) or []
        
        await message.edit_text(
            "🌍 *الخطوة 2/3:* استخراج المحتوى الكامل للمقالات...\n📖 قد يستغرق هذا من 30 إلى 60 ثانية...",
            parse_mode='Markdown'
        )
        
        # Enhance articles with full content (daily)
        enhanced_hajgov = enhance_articles_with_content(recent_hajgov, max_articles=30) or []
        enhanced_cnn = enhance_articles_with_content(recent_cnn, max_articles=20) or []
        all_enhanced_articles = enhanced_hajgov + enhanced_cnn
        
        with open("all_enhanced_hajj_articles.txt", "w", encoding="utf-8") as f:
            json.dump(all_enhanced_articles, f, ensure_ascii=False, indent=2)
        
        await message.edit_text(
            "🌍 *الخطوة 3/3:* إنهاء إعداد موجز الأخبار...",
            parse_mode='Markdown'
        )
        
        # Format the message - pass hajgov as newsapi, cnn as gnews, empty for rest
        news_message, total_pages, current_category, relevant_articles = format_news_message(
            enhanced_hajgov, enhanced_cnn, [], [], [], page, category
        )
        
        # Update message header for presenter style
        if category:
            news_message = f"🕋 *موجز أخبار الحج والعمرة - {category}*\n" + news_message[news_message.find('\n')+1:]
        else:
            news_message = f"🕋 *موجز أخبار الحج والعمرة اليومي*\n" + news_message[news_message.find('\n')+1:]
        
        # Create keyboard based on context
        keyboard = []
        
        if category:
            # Category view with pagination
            if total_pages > 1:
                nav_row = []
                if page > 1:
                    nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f'category_{category}_{page-1}'))
                if page < total_pages:
                    nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f'category_{category}_{page+1}'))
                if nav_row:
                    keyboard.append(nav_row)
            
            # Add PDF download button for category
            keyboard.append([InlineKeyboardButton("📄 تحميل تقرير الأخبار", callback_data=f'pdf_{category}')])
            keyboard.extend([
                [InlineKeyboardButton("🔄 تحديث جديد", callback_data=f'category_{category}_1')],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data='main_menu')]
            ])
        else:
            # Main view
            keyboard = [
                [InlineKeyboardButton("📄 تحميل التقرير الكامل", callback_data='pdf_all')],
                [InlineKeyboardButton("🔄 تحديث جديد", callback_data='get_news')],
                [InlineKeyboardButton("📝 توليد تقارير أسبوعية", callback_data='generate_weekly')],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data='main_menu')]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update the message with final results
        await message.edit_text(
            news_message,
            parse_mode='Markdown',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
            
    except Exception as e:
        error_message = f"❌ حدث خطأ أثناء إعداد موجز الأخبار: {str(e)}"
        logger.error(f"News briefing error: {str(e)}")
        await message.edit_text(error_message)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show category selection menu."""
    categories_message = """
🕋 *تصنيفات أخبار الحج والعمرة*

اختر تصنيفًا لاستكشاف الأخبار مع استخراج المحتوى الكامل:

• 🕌 **خدمات الحجاج** - النقل، الإسكان، التفويج، التصاريح، الحملات
• 📋 **التنظيم والإدارة** - الوزارة، الهيئات، الخطط، الاستعدادات
• 💡 **التقنية والابتكار** - التطبيقات، المنصات الرقمية، الذكاء الاصطناعي
• 🏥 **الصحة والسلامة** - الخدمات الطبية، الوقاية، الأمن والسلامة
• 📰 **أخبار عامة** - أخبار حج وعمرة متنوعة

*🆕 مزايا محسّنة لكل تصنيف:*
📖 استخراج المحتوى الكامل للمقالات
🧠 ملخصات ذكية مخصصة لكل تصنيف
📄 تقارير PDF مفصلة مع محتوى كامل
    """
    
    keyboard = [
        [InlineKeyboardButton("🕌 خدمات الحجاج", callback_data='category_خدمات الحجاج_1')],
        [InlineKeyboardButton("📋 التنظيم والإدارة", callback_data='category_التنظيم والإدارة_1')],
        [InlineKeyboardButton("💡 التقنية والابتكار", callback_data='category_التقنية والابتكار_1')],
        [InlineKeyboardButton("🏥 الصحة والسلامة", callback_data='category_الصحة والسلامة_1')],
        [InlineKeyboardButton("📰 أخبار عامة", callback_data='category_أخبار عامة_1')],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data='main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Always send a new message instead of editing
    if update.callback_query:
        await update.callback_query.message.reply_text(
            categories_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            categories_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

def format_news_message(hajgov_articles, cnn_articles, extra_articles=None, extra2=None, extra3=None, page=1, category=None):
    """Format news for Telegram message with pagination and categories"""
    articles_per_page = 6
    all_articles = (hajgov_articles or []) + (cnn_articles or []) + (extra_articles or []) + (extra2 or []) + (extra3 or [])

    
    if category and category in ['خدمات الحجاج', 'التنظيم والإدارة', 'التقنية والابتكار', 'الصحة والسلامة', 'أخبار عامة']:
        # Show articles from specific category
        categorized = categorize_articles(all_articles)
        category_articles = categorized.get(category, [])
        total_pages = (len(category_articles) + articles_per_page - 1) // articles_per_page if category_articles else 1
        start_idx = (page - 1) * articles_per_page
        end_idx = start_idx + articles_per_page
        page_articles = category_articles[start_idx:end_idx]
        
        category_label = category

        message = f"🕋 *أخبار الحج والعمرة - {category_label}* (محسّنة)\n"
        message += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        message += f"📄 الصفحة {page} من {total_pages} | عدد المقالات: {len(category_articles)}\n"
        
        # Show content extraction stats
        enhanced_count = len([a for a in category_articles if a and a.get('full_content')])
        message += f"📚 مقالات تم استخراج محتواها بالكامل: {enhanced_count}/{len(category_articles)}\n\n"
        
        message += f"📰 *المقالات (الصفحة {page}):*\n"
        for i, article in enumerate(page_articles, start_idx + 1):
            if not article:
                continue
            title = article.get('title', 'No title')
            source = article.get('source', {}).get('name', 'Unknown') if article.get('source') else 'Unknown'
            url = article.get('url', '')
            extraction_method = article.get('extraction_method', 'N/A')
            content_length = article.get('content_length', 0)
            
            if len(title) > 65:
                title = title[:62] + "..."
            
            message += f"{i}. {title}\n"
            message += f"   🏢 المصدر: {source} | 🔧 طريقة الاستخراج: {extraction_method}\n"
            message += f"   📊 طول المحتوى: {content_length} حرفًا\n"
            if url:
                message += f"   🔗 [قراءة التفاصيل]({url})\n"
            message += "\n"
        
        return message, total_pages, category, category_articles
    
    else:
        # Show main summary with top articles
        message = f"⭐ *تحديث أخبار الحج والعمرة* (محسّن مع المحتوى الكامل)\n"
        message += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        
        # Summary Statistics
        total_articles = len(all_articles)
        enhanced_count = len([a for a in all_articles if a and a.get('full_content')])       
        # Top Articles Preview
        message += f"📰 *أفضل المقالات المحسّنة اليوم:*\n"
        
        # Show top 10 articles total
        top_articles = all_articles[:10]
        for i, article in enumerate(top_articles, 1):
            if not article:
                continue
            title = article.get('title', 'No title')
            source = article.get('source', {}).get('name', 'Unknown') if article.get('source') else 'Unknown'
            url = article.get('url', '')
            extraction_method = article.get('extraction_method', 'N/A')
            content_length = article.get('content_length', 0)
            
            if len(title) > 65:
                title = title[:62] + "..."
            
            message += f"{i}. {title}\n"
            if url:
                message += f"   🔗 [قراءة التفاصيل]({url})\n"
            message += "\n"
        
        return message, 1, None, all_articles

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    
    if query.data == 'get_news':
        await get_news(update, context)
    elif query.data == 'generate_weekly':
        await generate_weekly_blogs(update, context)
    elif query.data == 'generate_monthly':
        await generate_monthly_blogs(update, context)
    elif query.data == 'generate_magazine':
        await generate_magazine(update, context)
    elif query.data == 'show_categories':
        await show_categories(update, context)
    elif query.data == 'help':
        await help_command(update, context)
    elif query.data == 'main_menu':
        await start(update, context)
    elif query.data.startswith('pdf_'):
        # Handle PDF generation
        category = query.data.replace('pdf_', '')
        if category == 'all':
            await generate_pdf_report(update, context, None)
        else:
            await generate_pdf_report(update, context, category)
    elif query.data.startswith('category_'):
        # Handle category navigation
        parts = query.data.split('_')
        if len(parts) >= 3:
            category = '_'.join(parts[1:-1])  # Reconstruct category name
            page = int(parts[-1])
            await get_news(update, context, page, category)

async def keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow the user to set or clear Hajj and Umrah-specific keywords."""
    message = update.message or update.effective_message
    if not message:
        return
    
    user_input = ''
    if context.args:
        user_input = ' '.join(context.args).strip()
    
    if user_input:
        lowered = user_input.lower()
        if lowered in ('clear', 'reset', 'remove', 'none'):
            context.user_data.pop('blog_keywords', None)
            context.user_data.pop('awaiting_keywords_input', None)
            await message.reply_text("🧹 تم مسح الكلمات المفتاحية المحفوظة. استخدم الأمر /keywords لإضافة كلمات جديدة في أي وقت.")
            return
        
        parsed_kw = parse_keyword_input(user_input)
        if parsed_kw:
            context.user_data['blog_keywords'] = parsed_kw
            context.user_data.pop('awaiting_keywords_input', None)
            await message.reply_text(f"✅ تم حفظ الكلمات المفتاحية!\n{keywords_summary_text(parsed_kw)}")
        else:
            await message.reply_text(
                "⚠️ يرجى استخدام الصيغة التالية (بالإنجليزية):\n"
                "`Primary Keyword | secondary keyword 1, secondary keyword 2`\n"
                "مثال:\n"
                "`Hajj News 2026 | hajj, umrah, pilgrimage, makkah`",
                parse_mode='Markdown'
            )
        return
    
    context.user_data['awaiting_keywords_input'] = True
    await message.reply_text(KEYWORD_INPUT_INSTRUCTIONS, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    raw_text = (update.message.text or '').strip()
    if not raw_text:
        return
    
    if context.user_data.get('awaiting_keywords_input'):
        lowered = raw_text.lower()
        if lowered in ('cancel', 'stop', 'skip', 'exit'):
            context.user_data.pop('awaiting_keywords_input', None)
            await update.message.reply_text("تم إلغاء إدخال الكلمات المفتاحية. استخدم الأمر /keywords عندما تكون جاهزًا.")
            return
        parsed_kw = parse_keyword_input(raw_text)
        if parsed_kw:
            context.user_data['blog_keywords'] = parsed_kw
            context.user_data.pop('awaiting_keywords_input', None)
            await update.message.reply_text(f"✅ تم حفظ الكلمات المفتاحية!\n{keywords_summary_text(parsed_kw)}")
        else:
            await update.message.reply_text(
                "⚠️ لم أتمكن من فهم الصيغة.\n"
                "يرجى الإرسال بالشكل التالي (بالإنجليزية):\n"
                "`Primary Keyword | secondary keyword 1, secondary keyword 2`\n"
                "مثال: `Hajj News 2026 | hajj, umrah, pilgrimage, makkah`",
                parse_mode='Markdown'
            )
        return
    
    if '|' in raw_text and not raw_text.startswith('/'):
        parsed_kw = parse_keyword_input(raw_text)
        if parsed_kw:
            context.user_data['blog_keywords'] = parsed_kw
            context.user_data.pop('awaiting_keywords_input', None)
            await update.message.reply_text(f"✅ تم حفظ الكلمات المفتاحية!\n{keywords_summary_text(parsed_kw)}")
            return
        else:
            await update.message.reply_text(
                "⚠️ يبدو أن هذه صيغة كلمات مفتاحية، لكن لم أتمكن من تحليلها.\n"
                "يرجى الإرسال بهذه الصيغة (بالإنجليزية): `Primary Keyword | secondary keyword 1, secondary keyword 2` "
                "أو استخدم الأمر /keywords.",
                parse_mode='Markdown'
            )
            return
    
    text = raw_text.lower()
    
    if any(word in text for word in ['news', 'hajj', 'حج', 'عمرة', 'update', 'enhanced', 'أخبار', 'حجاج']):
        await get_news(update, context)
    elif any(word in text for word in ['weekly', 'week', 'أسبوع', 'أسبوعي']):
        await generate_weekly_blogs(update, context)
    elif any(word in text for word in ['monthly', 'month', 'شهر', 'شهري']):
        await generate_monthly_blogs(update, context)
    elif any(word in text for word in ['magazine', 'مجلة', 'مجلات']):
        await generate_magazine(update, context)
    elif any(word in text for word in ['categories', 'category', 'topics', 'تصنيفات', 'تصنيف']):
        await show_categories(update, context)
    elif any(word in text for word in ['help', 'start', 'menu', 'مساعدة', 'بداية', 'قائمة']):
        await start(update, context)
    else:
        keyboard = [
        [InlineKeyboardButton("📰 الملخص اليومي", callback_data='get_news')],
        [InlineKeyboardButton("📊 الملخص الأسبوعي", callback_data='generate_weekly'),
         InlineKeyboardButton("📅 الملخص الشهري", callback_data='generate_monthly')],
        [InlineKeyboardButton("📰 المجلة", callback_data='generate_magazine')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⭐ أهلاً بك! أنا بوت أخبار الحج والعمرة المحسّنة مع استخراج كامل لمحتوى المقالات.\n\n"
            "اختر أحد الخيارات في الأسفل أو استخدم هذه الأوامر:\n"
            "• /news - الحصول على أخبار الحج والعمرة المحسّنة\n"
            "• /weekly - توليد تقارير/مدونات أسبوعية\n"
            "• /monthly - توليد تقارير/مدونات شهرية\n"
            "• /magazine - توليد مجلة الحج والعمرة الشهرية (PDF)\n"
            "• /keywords - إعداد الكلمات المفتاحية (بالإنجليزية) لتحسين محركات البحث\n"
            "• /categories - تصفح الأخبار حسب التصنيف\n"
            "• /help - المزيد من المعلومات",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    welcome_message = """
⭐ 👋 مرحباً بك! أنا مساعدك الإخباري الذكي لقطاع الحج والعمرة
تم تصميمي خصيصاً لأكون رفيقك اليومي في متابعة كل ما يخص أخبار وخدمات الحج والعمرة.
أقوم بجمع أحدث المستجدات، تحليلها، وتلخيصها لك بدقة واحترافية عالية،
لتكون دائماً في قلب الحدث دون إهدار وقتك في البحث بين المصادر المتعددة.

🤖 ملاحظة هامة:
أعتمد على خوارزميات الذكاء الاصطناعي المتقدمة لمعالجة وتلخيص الأخبار.
(هذه الخدمة تهدف لتسهيل المتابعة ولا تعتبر بديلاً عن التصريحات والقرارات الرسمية).

✨ أبرز ما أوفره لك:
📰 ملخصات يومية لأهم وأحدث أخبار القطاع.
📊 تقارير تحليلية شاملة ومفصلة (أسبوعية وشهرية).
📘 إصدارات شهرية متكاملة بصيغة PDF جاهزة للمشاركة.
⏱️ توفير الجهد والوقت لتبقَ مطلعاً على مدار الساعة.

🎯 لماذا تحتاجني؟
• لتكون على دراية تامة بمتغيرات السوق بشكل فوري.
• لتزويد فريق عملك وعملائك بتقارير دورية احترافية وموثوقة.
• لدعم اجتماعاتك الإدارية بملخصات دقيقة جاهزة للاستخدام.

🚀 جاهز للبدء؟
استخدم الخيارات والأزرار بالأسفل لاستكشاف الأخبار والتقارير.
    """
    
    keyboard = [
        [InlineKeyboardButton("⭐ الملخص اليومي", callback_data='get_news')],
        [InlineKeyboardButton("📊 التصنيفات", callback_data='show_categories')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Handle both regular messages and callback queries
    if update.callback_query:
        await update.callback_query.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

def call_claude_api(system_message, user_message, api_key=None, model=None, max_tokens=16384, temperature=0.7, use_cache=True, use_long_timeout=False):
    """
    Helper function to call AWS Bedrock Claude API.
    Maintains the same signature as the old API function for compatibility.
    
    Args:
        system_message: The system prompt
        user_message: The user prompt
        api_key: Not used (kept for compatibility), uses global client
        model: Not used (kept for compatibility), uses global inference profile
        max_tokens: Maximum tokens in response (default: 16384)
        temperature: Temperature setting (0.0-1.0)
        use_cache: Not used (kept for compatibility)
        use_long_timeout: If True, use 600s timeout (for long operations like magazine generation)
    
    Returns:
        tuple: (response_text, error_message) - error_message is None if successful
    """
    try:
        # Build messages array for AWS Bedrock Claude
        # Combine system and user message since Bedrock Claude uses a simple format
        # We'll prepend the system message to the user message for compatibility
        combined_content = user_message
        if system_message:
            combined_content = f"{system_message}\n\n{user_message}"
        
        messages = [
            {
                "role": "user",
                "content": combined_content
            }
        ]
        
        # Build request body for Bedrock Claude
        # Claude Sonnet 4.5 supports up to 64K output tokens
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": min(max_tokens, 64000),  # Claude Sonnet 4.5 supports up to 64K output
            "temperature": temperature,
            "messages": messages
        }
        
        # Select client based on timeout requirement
        client_to_use = bedrock_client_long if use_long_timeout else bedrock_client
        timeout_info = "600s (long operation)" if use_long_timeout else "60s (standard)"
        
        logger.info(f"Making AWS Bedrock API call - Inference Profile: {AWS_BEDROCK_INFERENCE_PROFILE}, Timeout: {timeout_info}")
        
        # Call AWS Bedrock API
        response = client_to_use.invoke_model(
            modelId=AWS_BEDROCK_INFERENCE_PROFILE,
            body=json.dumps(body)
        )
        
        logger.info(f"AWS Bedrock API call successful")
        
        # Parse response
        response_body = json.loads(response["body"].read())
        
        # Log stop reason for debugging
        stop_reason = response_body.get("stop_reason", "unknown")
        logger.info(f"AWS Bedrock response stop_reason: {stop_reason}")
        
        # Extract content from response
        if response_body.get("content") and len(response_body["content"]) > 0:
            content_text = response_body["content"][0].get("text", "")
            
            if not content_text:
                return None, "AWS Bedrock returned empty content"
            
            # Warn if response was truncated due to max_tokens
            if stop_reason == "max_tokens":
                logger.warning("⚠️ Response was truncated due to max_tokens limit!")
                logger.warning("   The model hit the output token limit before completing the response.")
                logger.warning("   Consider reducing the complexity of the request or splitting into multiple calls.")
            
            return content_text, None
        else:
            return None, "AWS Bedrock returned no content in response"
    
    except Exception as e:
        # Log the full exception for debugging
        error_type = type(e).__name__
        error_str = str(e)
        logger.error(f"AWS Bedrock API error: {error_type}: {error_str}")
        
        error_msg = error_str
        
        # Try to extract more specific error information from AWS Bedrock exceptions
        if hasattr(e, 'response'):
            try:
                if hasattr(e.response, 'get'):
                    error_data = e.response.get('Error', {})
                    if error_data:
                        error_msg = error_data.get('Message', error_msg)
                        error_code = error_data.get('Code', '')
                        logger.error(f"AWS Bedrock error code: {error_code}")
            except:
                pass
        
        # Check for specific error types
        if "ThrottlingException" in error_str or "Too many tokens" in error_str or "throttling" in error_str.lower():
            logger.error("⚠️ Rate limit exceeded - you've hit your daily token limit")
            logger.error("   Solutions:")
            logger.error("   1. Wait until your quota resets (usually daily)")
            logger.error("   2. Check your AWS Bedrock quotas: https://console.aws.amazon.com/servicequotas/")
            logger.error("   3. Request a quota increase if needed")
            logger.error("   4. Try a different model that has available quota")
            error_msg = f"Rate limit exceeded: {error_msg}"
        elif "ValidationException" in error_str or "validation" in error_str.lower():
            logger.error("⚠️ Validation error - check inference profile configuration")
            logger.error(f"   Inference Profile: {AWS_BEDROCK_INFERENCE_PROFILE}")
            logger.error(f"   Region: {AWS_REGION}")
            error_msg = f"Validation error: {error_msg}"
        elif "401" in error_str or "authentication" in error_str.lower() or "unauthorized" in error_str.lower():
            logger.error("⚠️ Authentication error - check AWS_BEARER_TOKEN_BEDROCK environment variable")
            error_msg = f"Authentication error: {error_msg}"
        
        return None, f"AWS Bedrock Error: {error_msg}"

def categorize_articles_for_blogs(articles):
    """Categorize Hajj and Umrah articles into two main blog themes"""
    
    if not articles:
        logger.warning("No articles provided to categorize_articles_for_blogs")
        return {
            'management': [],
            'improvement': []
        }
    
    # Blog 1: Pilgrim Services & Organization
    management_keywords = [
        'خدمات', 'حجاج', 'معتمرين', 'تفويج', 'نقل', 'إسكان', 'سكن',
        'إعاشة', 'تغذية', 'مخيمات', 'حملات', 'تصاريح', 'تأشيرات',
        'نسك', 'تنظيم', 'إدارة', 'وزارة', 'هيئة', 'إشراف', 'خطة'
    ]
    
    # Blog 2: Technology, Health & Innovation
    improvement_keywords = [
        'تقنية', 'تطبيق', 'رقمي', 'ذكاء اصطناعي', 'إلكتروني', 'منصة',
        'صحة', 'سلامة', 'طبي', 'مستشفى', 'إسعاف', 'وقاية',
        'ابتكار', 'تحول رقمي', 'تطوير', 'تحسين', 'أمن'
    ]
    
    management_articles = []
    improvement_articles = []
    general_articles = []
    
    for article in articles:
        if not article:
            continue
        title = article.get('title', '') or ''
        description = article.get('description', '') or ''
        full_content = article.get('full_content', '') or ''
        content = f"{title.lower()} {description.lower()} {full_content.lower()[:1000]}"
        
        management_score = sum(1 for keyword in management_keywords if keyword in content)
        improvement_score = sum(1 for keyword in improvement_keywords if keyword in content)
        
        if management_score > improvement_score and management_score > 0:
            management_articles.append(article)
        elif improvement_score > 0:
            improvement_articles.append(article)
        else:
            general_articles.append(article)
    
    # Distribute general articles
    half_general = len(general_articles) // 2
    management_articles.extend(general_articles[:half_general])
    improvement_articles.extend(general_articles[half_general:])
    
    return {
        'management': management_articles,
        'improvement': improvement_articles
    }

def parse_blog_sections(blog_content):
    """Parse blog content and return structured sections"""
    if not blog_content:
        logger.warning("No blog content provided to parse_blog_sections")
        return []
    
    sections = []
    current_section = {"title": "", "content": "", "level": 0}
    
    lines = blog_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Check for headers
        if line.startswith('#'):
            # Save previous section if it has content
            if current_section["content"].strip():
                sections.append(current_section.copy())
            
            # Start new section
            level = len(line) - len(line.lstrip('#'))
            title = line.lstrip('#').strip()
            
            current_section = {
                "title": title,
                "content": "",
                "level": level
            }
        else:
            # Add to current section content
            current_section["content"] += line + " "
    
    # Don't forget the last section
    if current_section["content"].strip():
        sections.append(current_section)
    
    return sections

def process_arabic_text(text):
    """Reshape and reorder Arabic text for correct display in PDF"""
    if not text:
        return ""
    reshaped_text = arabic_reshaper.reshape(text)
    bidi_text = get_display(reshaped_text)
    return bidi_text

def create_hajj_blog_pdf(blog_content, blog_title, is_temp_file=True):
    """Create a beautifully formatted Hajj and Umrah blog-style PDF"""
    
    if not blog_content or not blog_title:
        logger.warning("No blog content or title provided to create_hajj_blog_pdf")
        return None
    
    if is_temp_file:
        # Create a temporary file for Telegram bot
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        filename = temp_file.name
        temp_file.close()
    else:
        # Create with specific filename for standalone use
        filename = f"{blog_title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
    
    doc = SimpleDocTemplate(filename, pagesize=A4, 
                          topMargin=0.75*inch, bottomMargin=0.75*inch,
                          leftMargin=0.75*inch, rightMargin=0.75*inch)
    
    # Define comprehensive blog styles
    styles = getSampleStyleSheet()
    
    # Blog title style (main headline)
    blog_title_style = ParagraphStyle(
        'BlogTitle',
        parent=styles['Heading1'],
        fontSize=28,
        spaceAfter=15,
        spaceBefore=0,
        alignment=TA_CENTER,
        textColor=HexColor('#1a1a1a'),
        fontName='Amiri',
        leading=32
    )
    
    # Blog metadata style (date, info)
    blog_meta_style = ParagraphStyle(
        'BlogMeta',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=30,
        alignment=TA_CENTER,
        textColor=HexColor('#666666'),
        fontName='Amiri'
    )
    
    # Section header style (H2)
    section_header_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=18,
        spaceAfter=12,
        spaceBefore=24,
        textColor=HexColor('#2c3e50'),
        fontName='Amiri',
        alignment=TA_RIGHT
    )
    
    # Subsection header style (H3)
    subsection_header_style = ParagraphStyle(
        'SubsectionHeader',
        parent=styles['Heading3'],
        fontSize=14,
        spaceAfter=8,
        spaceBefore=16,
        textColor=HexColor('#34495e'),
        fontName='Amiri',
        alignment=TA_RIGHT
    )
    
    # Blog paragraph style
    blog_paragraph_style = ParagraphStyle(
        'BlogParagraph',
        parent=styles['Normal'],
        fontSize=12,
        spaceAfter=12,
        spaceBefore=0,
        alignment=TA_RIGHT,
        leading=18,
        textColor=HexColor('#333333'),
        fontName='Amiri'
    )
    
    # Build the document content
    content = []
    
    # Add logo at the top right corner if available
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logo_path = os.path.join(template_dir, 'images', 'Logo.png')
    if os.path.exists(logo_path):
        try:
            # Calculate available width (A4 width - left margin - right margin)
            available_width = A4[0] - (0.75*inch * 2)  # A4 width minus margins
            # Add logo with bigger size (5 inches wide, maintain aspect ratio) in top right corner
            logo = Image(logo_path, width=5*inch, height=1.25*inch, kind='proportional')
            # Use Table to position logo in top right corner
            logo_table = Table([[logo]], colWidths=[available_width])
            logo_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            content.append(logo_table)
            content.append(Spacer(1, 10))
        except Exception as e:
            logger.warning(f"Could not add logo to PDF: {str(e)}")
    
    # Add blog title
    content.append(Paragraph(process_arabic_text(blog_title), blog_title_style))
    
    # Add metadata
    date_str = datetime.now().strftime('%B %d, %Y')
    week_range = f"{(datetime.now() - timedelta(days=7)).strftime('%B %d')} - {datetime.now().strftime('%B %d, %Y')}"
    month_range = f"{(datetime.now() - timedelta(days=30)).strftime('%B %d')} - {datetime.now().strftime('%B %d, %Y')}"
    
    if 'يومي' in blog_title:
        meta_text = f"تقرير سياحي يومي • تم الإنشاء في {date_str}"
    elif 'أسبوعي' in blog_title:
        meta_text = f"تقرير سياحي أسبوعي • {week_range} • تم الإنشاء في {date_str}"
    elif 'شهري' in blog_title:
        meta_text = f"تقرير سياحي شهري • {month_range} • تم الإنشاء في {date_str}"
    else:
        meta_text = f"تقرير سياحي • تم الإنشاء في {date_str}"
    content.append(Paragraph(process_arabic_text(meta_text), blog_meta_style))
    
    content.append(Spacer(1, 10))
    
    # Parse the blog content into sections
    sections = parse_blog_sections(blog_content)
    
    for section in sections:
        title = section['title']
        section_content = section['content'].strip()
        level = section['level']
        
        # Skip empty sections
        if not section_content:
            continue
        
        # Add section header based on level
        if level == 1:
            continue  # Main title already added
        elif level == 2:
            if title:
                content.append(Paragraph(process_arabic_text(title), section_header_style))
        elif level == 3:
            if title:
                content.append(Paragraph(process_arabic_text(title), subsection_header_style))
        
        # Add section content
        if section_content:
            # Split into paragraphs
            paragraphs = section_content.split('. ')
            for para in paragraphs:
                para = para.strip()
                if para and len(para) > 20:
                    # Close the sentence if not ending with punctuation
                    if not para.endswith('.'):
                        para += '.'
                    content.append(Paragraph(process_arabic_text(para), blog_paragraph_style))
    
    # Build the PDF
    try:
        doc.build(content)
        logger.info(f"Successfully created Hajj news blog PDF: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Error creating Hajj news blog PDF: {str(e)}")
        return None

async def generate_pdf_report(update: Update, context: ContextTypes.DEFAULT_TYPE, category=None):
    """Generate and send enhanced PDF report with full content in daily blog style."""
    query = update.callback_query
    await query.answer()
    
    # Send status message
    status_message = await query.message.reply_text(
        "📄 جارٍ توليد تقرير PDF محسّن...\n📖 يتم الآن استخراج المحتوى الكامل لتحليل أكثر تفصيلاً...\n⏳ يرجى الانتظار من 1 إلى 2 دقيقة.",
        parse_mode='Markdown'
    )
    
    try:
        await status_message.edit_text(
            "📄 *الخطوة 1/2:* توليد محتوى تقريري بأسلوب مدونة سياحية...",
            parse_mode='Markdown'
        )
        
        # Load articles from saved file
        if not os.path.exists("all_enhanced_hajj_articles.txt"):
            await status_message.edit_text(
                "❌ لا توجد مقالات متاحة حاليًا. يرجى تشغيل الأمر /news أولاً لجلب المقالات.",
                parse_mode='Markdown'
            )
            return
        
        with open("all_enhanced_hajj_articles.txt", "r", encoding="utf-8") as f:
            all_articles = json.load(f)
        
        # Prepare articles according to scope
        user_keywords = get_user_keywords(context)
        if category and category != 'all':
            categorized = categorize_articles(all_articles)
            articles_for_report = categorized.get(category, [])
            blog_title = f"تقرير الحج والعمرة اليومي – {category}"
            blog_content = generate_daily_hajj_blog_with_ai(articles_for_report, category, keywords=user_keywords)
        else:
            articles_for_report = all_articles
            blog_title = "تقرير الحج والعمرة اليومي"
            blog_content = generate_daily_hajj_blog_with_ai(articles_for_report, None, keywords=user_keywords)

        # Fallback if model returned too-short, empty content, or error message
        if not blog_content or len(blog_content.strip()) < 100 or (blog_content.startswith("# التقرير اليومي للحج والعمرة") and "Error" in blog_content):
            logger.warning("Model returned empty/short content or error. Using fallback blog content.")
            blog_content = build_fallback_hajj_blog_content(articles_for_report, category)
        
        # Build PDF using blog formatter for consistent look
        pdf_filename = create_hajj_blog_pdf(blog_content, blog_title, is_temp_file=True)
        report_title = blog_title
        
        await status_message.edit_text(
            "📄 *الخطوة 2/2:* إنشاء ملف PDF...",
            parse_mode='Markdown'
        )
        
        # Send the PDF file
        if pdf_filename and os.path.exists(pdf_filename):
            with open(pdf_filename, 'rb') as pdf_file:
                await query.message.reply_document(
                    document=pdf_file,
                    filename=f"{report_title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf",
                    caption=f"📄 *{report_title}*\n📅 تاريخ الإنشاء: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n📖 تقرير محسّن مع استخراج كامل لمحتوى المقالات",
                    parse_mode='Markdown'
                )
            
            # Clean up the temporary file
            os.unlink(pdf_filename)
            
            # Update status message
            await status_message.edit_text(
                "✅ تم توليد تقرير PDF محسّن وإرساله بنجاح!\n📖 يشمل محتوى كاملًا للمقالات وتحليلًا سياحيًا تفصيليًا.",
                parse_mode='Markdown'
            )
        else:
            await status_message.edit_text(
                "❌ خطأ: تعذر إنشاء ملف PDF.",
                parse_mode='Markdown'
            )
        
    except Exception as e:
        try:
            await status_message.edit_text(
                f"❌ حدث خطأ أثناء توليد تقرير PDF المحسّن: {str(e)}",
                parse_mode='Markdown'
            )
        except Exception as edit_error:
            # Try to send a new message instead
            try:
                await status_message.reply_text(
                    f"❌ حدث خطأ أثناء توليد تقرير PDF المحسّن: {str(e)}"
                )
            except Exception as reply_error:
                logger.error(f"Error sending reply: {reply_error}")

def generate_daily_hajj_blog_with_ai(articles, category=None, keywords=None):
    """Generate a daily Hajj and Umrah blog-style summary so PDFs match weekly blog formatting."""
    if not articles:
        logger.warning("No articles provided to generate_daily_hajj_blog_with_ai")
        return "# التقرير اليومي للحج والعمرة\n\nلا توجد مقالات متاحة اليوم."
    
    # Prepare content from articles (shorter excerpts for daily)
    max_daily_articles = min(len(articles), 40)
    news_content = ""
    for i, article in enumerate(articles[:max_daily_articles], 1):
        if not article:
            continue
        title = article.get('title', 'No title')
        source = article.get('source', {}).get('name', 'Unknown source') if article.get('source') else 'Unknown source'
        full_content = article.get('full_content', article.get('description', 'No content'))
        published_date = article.get('publishedAt', 'Unknown date')
        url = article.get('url', '')
        if full_content and len(full_content) > 450:
            full_content = full_content[:450] + "..."
        news_content += f"""
ARTICLE {i}:
Title: {title}
Source: {source}
Date: {published_date}
URL: {url}
Content: {full_content or 'No content available'}
---
"""
    
    # Choose focus
    if category in [
        "خدمات الحجاج",
        "التنظيم والإدارة",
        "التقنية والابتكار",
        "الصحة والسلامة",
        "أخبار عامة"
    ]:
        title_suffix = f" – {category}"
        intro_target = f"أهم تطورات {category} اليوم"
    else:
        title_suffix = ""
        intro_target = "أهم تطورات الحج والعمرة اليوم"
    
    # System message (static, will be cached)
    system_message = (
        "You are a professional Arabic writer. "
        "You write concise, structured daily blog reports about Hajj, Umrah, and pilgrimage news in MODERN STANDARD ARABIC. "
        "All visible content, headings, and paragraphs must be in Arabic, but you may read/analyze English source text. "
        "Keep the style صحفي احترافي وسهل القراءة، واستخدم عناوين Markdown."
    )
    
    keyword_guidance = build_keyword_instruction_block(keywords)
    
    user_prompt = f"""
    {keyword_guidance}

    اكتب تقريرًا يوميًا موجزًا بأسلوب مدونة عن {intro_target} باللغة العربية الفصحى،
    مستخدمًا البنية التالية **بالضبط** باستخدام Markdown. اجعل النص مركزًا وغنيًا بالمعلومات.

# [اكتب عنوانًا عربيًا جذابًا لليوم]

## نظرة سريعة
[فقرة من 80–120 كلمة تلخص أهم محاور اليوم والعناوين الرئيسية في أخبار الحج والعمرة]

## أبرز الأخبار
[2-3 فقرات قصيرة، كل منها 80–120 كلمة، تربط بين أهم التحديثات في مجال الحج والعمرة]

## تطورات لافتة
[قائمة نقطية من 6–8 عناصر مختصرة، كل عنصر 1–2 جملة، تشير إلى شركات أو معايير أو نتائج محددة]

## السوق والتأثير
[1–2 فقرة عن تأثير الأخبار على قطاع الحج والعمرة والصناعة]

## ما الذي نترقبه لاحقًا
[3–5 نقاط حول الإعلانات المتوقعة أو الاتجاهات في مجال الحج والعمرة الصاعدة]

متطلبات أساسية:
- استخدم عناوين الأقسام العربية أعلاه كما هي مع تنسيق Markdown (##).
- امزج المعلومات من عدة مقالات، ولا تكتفِ بسردها واحدة تلو الأخرى.
- اذكر الأسماء والأرقام والمعايير والمنظمات كلما أمكن ذلك.
- اجعل الأسلوب صحفيًا احترافيًا وواضحًا، مناسبًا لتقرير يومي عن الحج والعمرة.
- ركّز دائمًا على صلة المحتوى بمجال الحج والعمرة.

مقالات للتحليل ({max_daily_articles} مقالاً):
{news_content}
"""
    
    # Call AWS Bedrock Claude API
    content, error = call_claude_api(
        system_message=system_message,
        user_message=user_prompt,
        max_tokens=2200,
        temperature=0.45,
        use_cache=True
    )
    
    if error:
        return f"# التقرير اليومي للحج والعمرة{title_suffix}\n\nحدث خطأ أثناء توليد المحتوى: {error}"
    
    if not content:
        return f"# التقرير اليومي للحج والعمرة{title_suffix}\n\nتعذّر توليد المحتوى اليوم."
    
    if not content.lstrip().startswith('#'):
        prefix_title = f"# التقرير اليومي للحج والعمرة{title_suffix}\n\n"
        return prefix_title + content
    
    logger.info(f"Model content length: {len(content)}")
    return content

def build_fallback_hajj_blog_content(articles, category=None):
    """Build a minimal, readable daily report from available articles when the model response is empty."""
    heading = f"# التقرير اليومي للحج والعمرة – {category}" if category else "# التقرير اليومي للحج والعمرة"
    if not articles:
        return f"{heading}\n\nلا توجد مقالات متاحة اليوم."
    lines = [heading, "", "## أهم العناوين", ""]
    count = 0
    for art in articles:
        if not art:
            continue
        title = art.get('title') or art.get('headline') or art.get('name')
        desc = art.get('description') or art.get('summary') or art.get('excerpt') or art.get('full_content', '')[:200]
        if not title and not desc:
            continue
        bullet = f"- {title.strip()}" if title else "- (بدون عنوان)"
        if desc:
            bullet += f" — {desc.strip()[:240]}"
        lines.append(bullet)
        count += 1
        if count >= 20:
            break
    if count == 0:
        lines.append("- لا توجد عناصر قابلة للعرض.")
    lines += ["", "## ملاحظات", "تم إنشاء هذا الملخص الاحتياطي بسبب عدم توفر استجابة من نموذج الذكاء الاصطناعي."]
    return "\n".join(lines)

def generate_hajj_blog_with_ai(articles, blog_theme, time_period="weekly", keywords=None):
    """Generate a Hajj and Umrah blog post using Claude AI"""
    
    if not articles:
        logger.warning(f"No articles provided to generate_hajj_blog_with_ai for {blog_theme}")
        return "تعذّر إنشاء مدونة الحج والعمرة: لا توجد مقالات كافية للتحليل."
    
    # Prepare content from articles
    news_content = ""
    article_count = min(len(articles), 30)
    
    for i, article in enumerate(articles[:article_count], 1):
        if not article:
            continue
        title = article.get('title', 'No title')
        source = article.get('source', {}).get('name', 'Unknown source') if article.get('source') else 'Unknown source'
        full_content = article.get('full_content', article.get('description', 'No content'))
        published_date = article.get('publishedAt', 'Unknown date')
        url = article.get('url', '')
        
        if full_content and len(full_content) > 600:
            full_content = full_content[:600] + "..."
        
        news_content += f"""
ARTICLE {i}:
Title: {title}
Source: {source}
Date: {published_date}
URL: {url}
Content: {full_content or 'No content available'}
---
"""
    
    # Determine period-specific language (Arabic labels)
    period_adj = "أسبوعية" if time_period == "weekly" else "شهرية"
    period_cap = "هذا الأسبوع" if time_period == "weekly" else "هذا الشهر"
    period_next = "الأسبوع القادم" if time_period == "weekly" else "الشهر القادم"
    
    # Create theme-specific prompts
    if blog_theme == "management":
        blog_focus = "خدمات الحجاج والتنظيم"
        blog_angle = (
            "ركّز على تطورات خدمات الحجاج، والتنظيم، والاستعدادات، "
            "والتشريعات والسياسات المتعلقة بالحج والعمرة، والتطورات في قطاع الحج. "
            "الجمهور المستهدف هو المسؤولون عن الحج والعمرة، والمنظمون، والمهتمون بالقطاع. "
            "أبرز استراتيجيات خدمة الحجاج، والتطوير، والشراكات، والابتكارات في القطاع."
        )
    elif blog_theme == "combined":
        blog_focus = "أخبار الحج والعمرة الشاملة"
        blog_angle = (
            "ركّز على كافة جوانب الحج والعمرة بما في ذلك خدمات الحجاج، التنظيم الإداري، التقنية والابتكار، "
            "والتطورات الصحية والأمنية. "
            "الجمهور المستهدف هو المتابعون الشاملون لقطاع الحج والعمرة والمهتمون بجميع مستجداته. "
            "أبرز أهم الأخبار والقرارات والتطورات التكنولوجية والتنظيمية في القطاع."
        )
    else:  # improvement
        blog_focus = "التقنية والصحة والابتكار في الحج"
        blog_angle = (
            "ركّز على التقنية والابتكار والتحول الرقمي في خدمة الحجاج والمعتمرين، "
            "والخدمات الصحية والسلامة والأمن خلال موسم الحج، والمبادرات التطويرية. "
            "الجمهور المستهدف هو المهتمون بتطوير خدمات الحج، والمنظّمون، ومزودو الخدمات. "
            "أبرز الاتجاهات الصاعدة، والابتكارات، وأفضل الممارسات في خدمة ضيوف الرحمن."
        )
    
    system_message = (
        "You are a professional Arabic Hajj and Umrah industry blogger. "
        "You always write engaging, insightful blog posts in MODERN STANDARD ARABIC about Hajj, Umrah, and pilgrimage developments. "
        "Use clear structure, strong headings in Arabic, and actionable insights. "
        "Always use proper markdown formatting for headers, and keep the tone صحفي احترافي وجذّاب."
    )
    
    keyword_guidance = build_keyword_instruction_block(keywords)
    
    user_prompt = f"""
    {keyword_guidance}

    اكتب تدوينة {period_adj} عربية شاملة عن {blog_focus} خلال {period_cap}،
    مستخدمًا البنية التالية **بالضبط** باستخدام Markdown:

    # [اكتب عنوانًا عربيًا جذابًا]

    ## مقدمة
    [مقدمة مشوّقة من 150 كلمة تقريبًا تجذب القارئ وتشرح سياق التقرير]

    ## أهم قصة في {period_cap}
    [250–300 كلمة تغطي التطور الأهم في أخبار الحج والعمرة لهذا {period_cap}]

    ## تطور رئيسي ثانٍ
    [250–300 كلمة عن ثاني أهم تطور]

    ## اتجاهات بارزة
    [200–250 كلمة عن أبرز الاتجاهات والأنماط الملحوظة]

    ## تركيز على معيار أو قطاع
    [200–250 كلمة تبرز معايير أو شركات أو قطاعات محددة]

    ## ملخصات سريعة
    [200–250 كلمة تغطي 6–8 تطورات إضافية بشكل موجز]

    ## مراقبة السوق
    [150–200 كلمة عن الاستثمارات، الشراكات، وأخبار الأعمال في مجال الحج والعمرة]

    ## ما الذي ينتظرنا لاحقًا
    [100–150 كلمة تستشرف ما قد يحدث في {period_next}]

    ## خلاصة
    [فقرة ختامية قصيرة بأهم الرسائل والتوصيات]

    زاوية التغطية:
    {blog_angle}

    متطلبات أساسية:
    - يجب استخدام عناوين الأقسام العربية أعلاه كما هي مع تنسيق Markdown (##).
    - استشهد بما لا يقل عن 15–20 مقالًا مختلفًا داخل التدوينة.
    - اذكر أسماء الشركات، المعايير، الأرقام، التواريخ، والمصادر كلما أمكن.
    - اجعل الأسلوب عربيًا صحفيًا مهنيًا وجذابًا.
    - اجعل كل قسم غنيًا بالمعلومات وقابلًا للاستخدام لخبراء الحج والعمرة.
    - أمامك {article_count} مقالًا، فاستخدم هذا التنوع في بناء الصورة الكلية.

    محتوى المقالات للتحليل ({article_count} مقالاً):
    {news_content}

    اكتب التدوينة باللغة العربية الفصحى فقط، بدون أي فقرات تفسيرية باللغة الإنجليزية.
    """
    
    content, error = call_claude_api(
        system_message=system_message,
        user_message=user_prompt,
        max_tokens=3500,
        temperature=0.5,
        use_cache=True
    )
    
    if error:
        return f"حدث خطأ أثناء توليد التدوينة: {error}"
    else:
        return content

async def generate_weekly_blogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate comprehensive weekly Hajj and Umrah blog posts."""
    user_id = get_user_id(update)
    
    # Check usage limit
    has_limit, current_usage = check_usage_limit(user_id, 'weekly')
    if not has_limit:
        limit_message = (
            f"❌ *تم الوصول إلى الحد الأقصى*\n\n"
            f"لقد استخدمت جميع المحاولات المتاحة للتقارير الأسبوعية ({USAGE_LIMITS['weekly']}/{USAGE_LIMITS['weekly']}).\n\n"
        )
        if update.callback_query:
            await update.callback_query.answer("تم الوصول إلى الحد الأقصى", show_alert=True)
            await update.callback_query.message.reply_text(limit_message, parse_mode='Markdown')
        else:
            await update.message.reply_text(limit_message, parse_mode='Markdown')
        return
    
    # Increment usage
    increment_usage(user_id, 'weekly')
    
    if update.callback_query:
        await update.callback_query.answer()
        message = await update.callback_query.message.reply_text(
            "📝 *مولّد المدونات الأسبوعية للحج والعمرة*\n\n⏳ جارٍ إعداد تحليل أسبوعي شامل...\n📊 سيتم تحليل أخبار الحج والعمرة لآخر 7 أيام\n⏰ الزمن المتوقع: 3–5 دقائق\n\nيرجى الانتظار...",
            parse_mode='Markdown'
        )
    else:
        message = await update.message.reply_text(
            "📝 *مولّد المدونات الأسبوعية للحج والعمرة*\n\n⏳ جارٍ إعداد تحليل أسبوعي شامل...\n📊 سيتم تحليل أخبار الحج والعمرة لآخر 7 أيام\n⏰ الزمن المتوقع: 3–5 دقائق\n\nيرجى الانتظار...",
            parse_mode='Markdown'
        )
    
    try:
        await message.edit_text(
            "📝 *الخطوة 1/4:* جلب أخبار الحج والعمرة الأسبوعية...\n📡 يتم الآن جمع المقالات من آخر 7 أيام...",
            parse_mode='Markdown'
        )
        
        hajgov_articles = fetch_hajgov_news() or []
        cnn_articles = fetch_cnn_hajj_news() or []

        logger.info(f"Fetched {len(hajgov_articles)} haj.gov.sa, {len(cnn_articles)} CNN Arabic")
        
        await message.edit_text(
            "📝 *الخطوة 2/4:* تصفية المقالات...\n🔍 تصفية أخبار الحج والعمرة...",
            parse_mode='Markdown'
        )
        
        # No filtering needed - sources are already Hajj-specific

        recent_hajgov = filter_recent_articles(hajgov_articles, days=7) or []
        recent_cnn = filter_recent_articles(cnn_articles, days=7) or []

        all_articles = recent_hajgov + recent_cnn
        logger.info(f"Total relevant articles: {len(all_articles)}")
        
        if not all_articles:
            await message.edit_text(
                "❌ لم يتم العثور على أخبار حج وعمرة كافية. يرجى المحاولة لاحقًا.",
                parse_mode='Markdown'
            )
            return
        
        await message.edit_text(
            f"📝 *الخطوة 3/4:* استخراج المحتوى الكامل...\n📖 جاري معالجة {min(len(all_articles), 50)} مقالات تقريبًا\n⏱️ قد يستغرق هذا من 2–3 دقائق...",
            parse_mode='Markdown'
        )
        
        enhanced_articles = enhance_articles_with_content(all_articles, max_articles=50, weekly_mode=True) or []
        enhanced_count = len([a for a in enhanced_articles if a.get('full_content')])
        logger.info(f"Enhanced articles: {enhanced_count}/{len(enhanced_articles)}")
        
        await message.edit_text(
            "📝 *الخطوة 4/6:* توليد تقرير أسبوعي باستخدام الذكاء الاصطناعي...\n✍️ يتم الآن إنشاء تحليل أسبوعي شامل...",
            parse_mode='Markdown'
        )
        
        user_keywords = get_user_keywords(context)
        
        logger.info(f"Total blog articles for combined report: {len(enhanced_articles)}")
        
        # Generate Combined Blog
        combined_blog = None
        if enhanced_articles:
            combined_blog = generate_hajj_blog_with_ai(
                enhanced_articles, "combined", "weekly", keywords=user_keywords
            )
        
        # Step 5: Create PDFs
        await message.edit_text(
            "📝 *الخطوة 5/6:* إنشاء ملفات PDF احترافية...\n📄 يتم الآن تنسيق التقرير...",
            parse_mode='Markdown'
        )
        
        combined_filename = None
        
        if combined_blog:
            combined_filename = create_hajj_blog_pdf(
                combined_blog,
                "التقرير الأسبوعي الشامل للحج والعمرة",
                is_temp_file=True
            )
        
        # Step 6: Send the blog PDFs
        await message.edit_text(
            "📝 *الخطوة 6/6:* إرسال ملفات PDF...\n📤 يتم الآن إرسال الرؤى والتحليلات الأسبوعية للحج والعمرة...",
            parse_mode='Markdown'
        )
        
        if combined_filename:
            try:
                with open(combined_filename, 'rb') as pdf_file:
                    await message.reply_document(
                        document=pdf_file,
                        filename=f"Hajj_Weekly_Report_{datetime.now().strftime('%Y%m%d')}.pdf",
                        caption="📝 **التقرير الأسبوعي الشامل للحج والعمرة**\n💼 تحليل شامل لكافة التطورات والأخبار في قطاع الحج والعمرة",
                        parse_mode='Markdown'
                    )
                os.unlink(combined_filename)
            except Exception as e:
                logger.error(f"Error sending combined PDF: {e}")
        
        # Success message with statistics
        combined_status = "Generated" if combined_blog else "Skipped (insufficient data)"
        
        success_message = f"""
 ✅ **تم الانتهاء من توليد التقرير الأسبوعي الشامل للحج والعمرة بنجاح!**

 📊 **إحصائيات المعالجة:**
 • إجمالي المقالات التي تم تحليلها: {len(enhanced_articles)}
 • نجاح استخراج المحتوى الكامل: {enhanced_count}/{len(enhanced_articles)} ({(enhanced_count/len(enhanced_articles)*100) if enhanced_articles else 0:.1f}%)
 • نطاق التغطية الأسبوعية: {(datetime.now() - timedelta(days=7)).strftime('%B %d')} - {datetime.now().strftime('%B %d, %Y')}

 📝 **التقارير التي تم توليدها:**
 • التقرير الأسبوعي الشامل للحج والعمرة - {combined_status}

 التقرير يحتوي على أقسام منظمة وتحليل متعمق وتنسيق احترافي!
        """
        
        keyboard = [
            [InlineKeyboardButton("🔄 توليد تقارير أسبوعية جديدة", callback_data='generate_weekly')],
            [InlineKeyboardButton("📰 الأخبار اليومية", callback_data='get_news')],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(
            success_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_message = f"❌ حدث خطأ أثناء توليد المدونات الأسبوعية: {str(e)}"
        logger.error(f"Weekly blog generation error: {str(e)}")
        await message.edit_text(error_message)

async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /weekly command directly."""
    await generate_weekly_blogs(update, context)

async def generate_monthly_blogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate comprehensive monthly Hajj and Umrah blog posts."""
    user_id = get_user_id(update)
    
    # Check usage limit
    has_limit, current_usage = check_usage_limit(user_id, 'monthly')
    if not has_limit:
        limit_message = (
            f"❌ *تم الوصول إلى الحد الأقصى*\n\n"
            f"لقد استخدمت جميع المحاولات المتاحة للتقارير الشهرية ({USAGE_LIMITS['monthly']}/{USAGE_LIMITS['monthly']}).\n\n"
        )
        if update.callback_query:
            await update.callback_query.answer("تم الوصول إلى الحد الأقصى", show_alert=True)
            await update.callback_query.message.reply_text(limit_message, parse_mode='Markdown')
        else:
            await update.message.reply_text(limit_message, parse_mode='Markdown')
        return
    
    # Increment usage
    increment_usage(user_id, 'monthly')
    
    if update.callback_query:
        await update.callback_query.answer()
        message = await update.callback_query.message.reply_text(
            "📝 *مولّد المدونات الشهرية للحج والعمرة*\n\n⏳ جارٍ إعداد تحليل شهري شامل...\n📊 سيتم تحليل أخبار الحج والعمرة لآخر 30 يومًا\n⏰ الزمن المتوقع: 5–10 دقائق\n\nيرجى الانتظار...",
            parse_mode='Markdown'
        )
    else:
        message = await update.message.reply_text(
            "📝 *مولّد المدونات الشهرية للحج والعمرة*\n\n⏳ جارٍ إعداد تحليل شهري شامل...\n📊 سيتم تحليل أخبار الحج والعمرة لآخر سنة\n⏰ الزمن المتوقع: 5–10 دقائق\n\nيرجى الانتظار...",
            parse_mode='Markdown'
        )
    
    try:
        await message.edit_text(
            "📝 *الخطوة 1/4:* جلب أخبار الحج والعمرة الشهرية...\n📡 يتم الآن جمع المقالات من آخر سنة...",
            parse_mode='Markdown'
        )
        
        hajgov_articles = fetch_hajgov_news() or []
        cnn_articles = fetch_cnn_hajj_news() or []

        logger.info(f"Fetched {len(hajgov_articles)} haj.gov.sa, {len(cnn_articles)} CNN Arabic")
        
        await message.edit_text(
            "📝 *الخطوة 2/4:* تصفية المقالات...\n🔍 تصفية أخبار الحج والعمرة...",
            parse_mode='Markdown'
        )
        
        # No filtering needed - sources are already Hajj-specific

        recent_hajgov = filter_recent_articles(hajgov_articles, days=365) or []
        recent_cnn = filter_recent_articles(cnn_articles, days=365) or []

        all_articles = recent_hajgov + recent_cnn
        logger.info(f"Total relevant articles: {len(all_articles)}")
        
        if not all_articles:
            await message.edit_text(
                "❌ لم يتم العثور على أخبار حج وعمرة كافية. يرجى المحاولة لاحقًا.",
                parse_mode='Markdown'
            )
            return
        
        await message.edit_text(
            f"📝 *الخطوة 3/4:* استخراج المحتوى الكامل...\n📖 جاري معالجة {min(len(all_articles), 100)} مقالات تقريبًا\n⏱️ قد يستغرق هذا من 5–8 دقائق...",
            parse_mode='Markdown'
        )
        
        enhanced_articles = enhance_articles_with_content(all_articles, max_articles=100, monthly_mode=True) or []
        enhanced_count = len([a for a in enhanced_articles if a.get('full_content')])
        logger.info(f"Enhanced articles: {enhanced_count}/{len(enhanced_articles)}")
        
        await message.edit_text(
            "📝 *الخطوة 4/6:* توليد تدوينات شهرية باستخدام الذكاء الاصطناعي...\\n✍️ يتم الآن إنشاء تحليلات شهرية شاملة...",
            parse_mode='Markdown'
        )
        
        user_keywords = get_user_keywords(context)
        categorized = categorize_articles_for_blogs(enhanced_articles)
        management_articles = categorized.get('management', []) or []
        improvement_articles = categorized.get('improvement', []) or []
        
        logger.info(f"Management blog articles: {len(management_articles)}, Improvement blog articles: {len(improvement_articles)}")
        
        # Generate Management Blog
        management_blog = None
        if management_articles:
            management_blog = generate_hajj_blog_with_ai(
                management_articles, "management", "monthly", keywords=user_keywords
            )
        
        # Generate Improvement Blog
        improvement_blog = None
        if improvement_articles:
            improvement_blog = generate_hajj_blog_with_ai(
                improvement_articles, "improvement", "monthly", keywords=user_keywords
            )
        
        # Step 5: Create PDFs
        await message.edit_text(
            "📝 *الخطوة 5/6:* إنشاء ملفات PDF احترافية...\\n📄 يتم الآن تنسيق التدوينات...",
            parse_mode='Markdown'
        )
        
        management_filename = None
        improvement_filename = None
        
        if management_blog:
            management_filename = create_hajj_blog_pdf(
                management_blog,
                "التقرير الشهري للحج والعمرة",
                is_temp_file=True
            )
        
        if improvement_blog:
            improvement_filename = create_hajj_blog_pdf(
                improvement_blog,
                "التقرير الشهري للتقنية والصحة والابتكار في الحج",
                is_temp_file=True
            )
        
        #  Step 6: Send the blog PDFs
        await message.edit_text(
            "📝 *الخطوة 6/6:* إرسال ملفات PDF...\\n📤 يتم الآن إرسال الرؤى والتحليلات الشهرية للحج والعمرة...",
            parse_mode='Markdown'
        )
        
        if management_filename:
            try:
                with open(management_filename, 'rb') as pdf_file:
                    await message.reply_document(
                        document=pdf_file,
                        filename=f"Hajj_Management_Monthly_{datetime.now().strftime('%Y%m%d')}.pdf",
                        caption="📝 **التقرير الشهري للحج والعمرة**\\n💼 تحليل شهري شامل لاتجاهات خدمات الحجاج وتطورات التنظيم والإدارة",
                        parse_mode='Markdown'
                    )
                os.unlink(management_filename)
            except Exception as e:
                logger.error(f"Error sending management PDF: {e}")
        
        if improvement_filename:
            try:
                with open(improvement_filename, 'rb') as pdf_file:
                    await message.reply_document(
                        document=pdf_file,
                        filename=f"Hajj_Tech_Innovation_Monthly_{datetime.now().strftime('%Y%m%d')}.pdf",
                        caption="📝 **التقرير الشهري للتقنية والصحة والابتكار في الحج**\\n⭐ تحليل شهري شامل لتطورات التقنية والابتكار وخدمات الضيوف",
                        parse_mode='Markdown'
                    )
                os.unlink(improvement_filename)
            except Exception as e:
                logger.error(f"Error sending improvement PDF: {e}")
        
        # Success message with statistics
        management_status = "Generated" if management_blog else "Skipped (insufficient data)"
        improvement_status = "Generated" if improvement_blog else "Skipped (insufficient data)"
        
        success_message = f"""
 ✅ **تم الانتهاء من توليد المدونات الشهرية للحج والعمرة بنجاح!**

 📊 **إحصائيات المعالجة:**
 • إجمالي المقالات التي تم تحليلها: {len(enhanced_articles)}
 • نجاح استخراج المحتوى الكامل: {enhanced_count}/{len(enhanced_articles)} ({(enhanced_count/len(enhanced_articles)*100) if enhanced_articles else 0:.1f}%)
 • عدد المقالات في مدونة خدمات الحجاج: {len(management_articles)}
 • عدد المقالات في مدونة التحسين والتميز: {len(improvement_articles)}
 • نطاق التغطية الشهرية: {(datetime.now() - timedelta(days=30)).strftime('%B %d')} - {datetime.now().strftime('%B %d, %Y')}

 📝 **التقارير التي تم توليدها:**
 • التقرير الشهري للحج والعمرة - {management_status}
 • التقرير الشهري للتقنية والصحة والابتكار في الحج - {improvement_status}

 كلا التقريرين يحتويان على أقسام منظمة وتحليل متعمق وتنسيق احترافي!
        """
        
        keyboard = [
            [InlineKeyboardButton("🔄 توليد تقارير شهرية جديدة", callback_data='generate_monthly')],
            [InlineKeyboardButton("📰 الأخبار اليومية", callback_data='get_news')],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(
            success_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_message = f"❌ حدث خطأ أثناء توليد المدونات الشهرية: {str(e)}"
        logger.error(f"Monthly blog generation error: {str(e)}")
        await message.edit_text(error_message)

async def monthly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /monthly command directly."""
    await generate_monthly_blogs(update, context)

# ============================================================================
# AI MAGAZINE FEATURE
# ============================================================================

def scrape_og_image(article_url: str, timeout_s: int = 10) -> str:
    """
    Extract the og:image / twitter:image from an article page.
    Returns an absolute image URL or '' on failure.
    Used to get article-specific images from their source URLs before falling back to generics.
    """
    if not article_url or not isinstance(article_url, str):
        return ""
    url = article_url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return ""
    # Skip Twitter/X.com URLs - they don't return useful OG images to bots
    if "x.com" in url or "twitter.com" in url:
        return ""
    try:
        resp = requests.get(
            url,
            timeout=timeout_s,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
        )
        if resp.status_code != 200 or not resp.text:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = []
        for key in ("og:image", "twitter:image", "twitter:image:src"):
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                candidates.append(tag["content"].strip())
        link = soup.find("link", attrs={"rel": "image_src"})
        if link and link.get("href"):
            candidates.append(link["href"].strip())
        for img in candidates:
            if not img:
                continue
            if img.startswith("//"):
                return "https:" + img
            if img.startswith("http://") or img.startswith("https://"):
                return img
    except Exception:
        pass
    return ""


def generate_magazine_content_with_ai(articles):
    """
    Generate structured JSON content for the monthly Hajj report using Claude.
    Returns (magazine_data, article_map) where article_map maps 1-based index -> article metadata.
    """
    if not articles:
        return None, {}

    # Prepare article context with image URLs - store mapping for later matching
    articles_context = ""
    article_map = {}  # Map article index to image URL and source for direct lookup
    for i, article in enumerate(articles[:40]):  # Limit to 40 articles for context
        title = article.get('title', 'No title')
        content = article.get('full_content', '')[:1000]  # Truncate for token limits
        # Get image URL from various possible fields
        image_url = (
            article.get('urlToImage') or
            article.get('image_url') or
            article.get('image') or
            ''
        )
        source = article.get('source', {}).get('name', '') if isinstance(article.get('source'), dict) else str(article.get('source', ''))
        articles_context += f"Article {i+1}: {title}\nSource: {source}\nImage: {image_url}\nContent: {content}\n\n"
        # Store for direct lookup by article_index
        article_map[i + 1] = {
            'image_url': image_url,
            'source': source,
            'title': title,
            'url': article.get('url', ''),
            'raw_article': article,
        }

    system_message = (
        "You are the Editor-in-Chief of a professional monthly Hajj and Umrah report. "
        "Your goal is to maintain a professional, insightful, and visionary tone. "
        "Critical page layout rule: Each article (including the first one) must fit exactly on one A4 page. "
        "NO EXCEPTIONS - All 8 articles must be between 300-350 words TOTAL (Lead + Main Content). "
        "Strict Enforcement: Count words for each article. If any article exceeds 350 words, it will overflow the page. "
        "If any article is under 270 words, it will have excessive whitespace. "
        "Target 310-330 words per article for optimal page fill without overflow. "
        "The first article is NOT special - it must follow the same word count rules as all other articles. "
        "Balance depth with brevity - provide comprehensive coverage but adhere to the strict 300-350 word limit. "
        "Output ONLY valid JSON matching the specified structure. "
        "CRITICAL: ALL text content (titles, subtitles, leads, articles, editors_note, highlights, locations) MUST be written in MODERN STANDARD ARABIC (العربية الفصحى). "
        "You may read English source articles but ALL output MUST be in Arabic."
    )

    user_prompt = f"""
    أنشئ محتوى مجلة الحج والعمرة الشهرية بناءً على هذه المقالات:
    {articles_context}

    أعد كائن JSON بهذه البنية بالضبط (بدون markdown، فقط JSON):
    {{
        "title": "تقرير الحج والعمرة: [عنوان جذاب بالعربية]",
        "subtitle": "[عنوان فرعي جذاب بالعربية]",
        "date": "[الشهر والسنة الحاليين بالعربية]",
        "highlights": [
            {{"title": "[عنوان 1 بالعربية]", "description": "[وصف قصير بالعربية]"}},
            {{"title": "[عنوان 2 بالعربية]", "description": "[وصف قصير بالعربية]"}},
            {{"title": "[عنوان 3 بالعربية]", "description": "[وصف قصير بالعربية]"}}
        ],
        "editors_note": "[حد أقصى 150 كلمة بالعربية. تعليق تحريري مهني وبصيرة حول أخبار الحج والعمرة.]",
        "articles": [
            {{
                "category": "[واحدة من: خدمات الحجاج, التقنية, الصحة والسلامة, التنظيم والإدارة]",
                "title": "[عنوان مجلة جذاب بالعربية]",
                "location": "[الموقع/المنطقة بالعربية، مثال: مكة المكرمة / السعودية]",
                "lead": "[فقرة افتتاحية جذابة بالعربية، 2-3 جمل (حوالي 40-50 كلمة). عدد الكلمات هذا مشمول في إجمالي 300-350.]",
                "content": "[المحتوى الرئيسي بتنسيق HTML بالعربية مع عناوين فرعية <h3> وفقرات <p>. عدد الكلمات الإجمالي (الافتتاحية + المحتوى) يجب أن يكون 300-350 كلمة بالضبط. المحتوى الرئيسي 250-300 كلمة. أنشئ 3-4 فقرات (حوالي 80 كلمة لكل منها) مع عنوانين فرعيين.]",
                "article_index": "[رقم المقال الأصلي من القائمة أعلاه، مثلاً 3 أو 7]",
                "source": "[اسم المصدر الأصلي]",
                "score": "[درجة الأهمية 1-10]"
            }},
            ... (أنشئ بالضبط 8 مقالات مميزة. لا تتجاوز 8.)
        ]
    }}

    مهم جداً:
    1. تأكد من أن جميع علامات الاقتباس المزدوجة داخل قيم النصوص مهرّبة بشكل صحيح بعلامة backslash (\\").
    2. لا تستخدم فواصل أسطر markdown أو فواصل زائدة تجعل JSON غير صالح.
    3. يجب أن يكون الإخراج سلسلة JSON واحدة صالحة.
    4. حقل article_index إلزامي لكل مقال - يجب أن يطابق رقم المقال في القائمة أعلاه (1-40). هذا يُستخدم لربط الصورة الصحيحة بشكل مباشر.
    5. تطبيق صارم لعدد الكلمات لجميع المقالات (بدون استثناءات):
       - إجمالي عدد الكلمات لكل مقالة (الافتتاحية + المحتوى) يجب أن يكون بين 300-350 كلمة.
       - الحد الأدنى: 300 كلمة.
       - الحد الأقصى: 350 كلمة.
       - النطاق المثالي: 310-330 كلمة لكل مقالة.
    6. جميع النصوص يجب أن تكون باللغة العربية الفصحى.
    """

    logger.info("Calling AWS Bedrock Claude API for magazine content generation...")
    content_text, error = call_claude_api(
        system_message=system_message, 
        user_message=user_prompt, 
        max_tokens=50000,
        temperature=0.7,
        use_long_timeout=True  # Use 600 second timeout for magazine generation
    )

    if error:
        logger.error(f"Magazine generation error (AWS Bedrock): {error}")
        logger.error(f"Error type: {type(error)}")
        return None

    if not content_text:
        logger.error("Magazine generation returned empty content")
        return None

    try:
        # Clean potential markdown fences
        json_str = content_text.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()
        
        # Log the length of the response for debugging
        logger.info(f"Magazine JSON response length: {len(json_str)} characters")
        
        # Check if the JSON appears to be truncated (unterminated string or brace)
        if not json_str.endswith('}'):
            logger.warning("JSON response appears to be truncated (doesn't end with })")
            logger.error(f"JSON string ending: ...{json_str[-200:]}")
            return None
        
        magazine_data = json.loads(json_str)
        return magazine_data, article_map
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode magazine JSON: {e}")
        logger.error(f"JSON decode error at line {e.lineno}, column {e.colno}")
        logger.error(f"JSON string preview (first 500 chars):\n{json_str[:500]}")
        logger.error(f"JSON string ending (last 500 chars):\n...{json_str[-500:]}")
        logger.error(f"Full JSON length: {len(json_str)} characters")
        
        # Try to identify if this is a truncation issue
        if "Unterminated string" in str(e) or "Expecting" in str(e):
            logger.error("⚠️ This appears to be a truncated response. The model may have hit the max_tokens limit.")
            logger.error("   Possible solutions:")
            logger.error("   1. Reduce the number of articles in the magazine (currently 8)")
            logger.error("   2. Simplify the article content requirements")
            logger.error("   3. Split magazine generation into multiple API calls")
        
        return None, {}

def render_newspaper_pdf(content_data, output_filename="newspaper.pdf"):
    """
    Render newspaper-style PDF using Jinja2 and WeasyPrint.
    """
    if not WEASYPRINT_AVAILABLE:
        logger.error("WeasyPrint not available (missing GTK or module). Cannot generate PDF.")
        return None

    try:
        # Setup Jinja2
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template('newspaper.html')
        
        # Inject default images if available
        import glob
        import random
        import pathlib
        
        images_dir = os.path.join(template_dir, 'images')
        available_images = []
        if os.path.exists(images_dir):
            all_images = (
                glob.glob(os.path.join(images_dir, '*.jpg')) +
                glob.glob(os.path.join(images_dir, '*.png')) +
                glob.glob(os.path.join(images_dir, '*.jpeg')) +
                glob.glob(os.path.join(images_dir, '*.webp'))
            )
            # Filter out cover images
            exclude_files = ['Cover.png', 'cover.png', 'cover_generated.png', 'back_cover_generated.png', 'Back Cover.png', 'Back Cover.jpg', 'back cover.png', '1767655448098.jpg','Logo.jpg']
            available_images = [
                img for img in all_images 
                if os.path.basename(img) not in exclude_files
            ]
        
        # Assign images to articles (round-robin or random)
        articles = content_data.get('articles', [])
        for article in articles:
            if available_images and not article.get('local_image_path') and not article.get('image_url'):
                # Convert to file URI safely handling spaces/OS specifics
                img_path = random.choice(available_images)
                article['local_image_path'] = pathlib.Path(img_path).as_uri()
        
        # Batch articles into pages (2 articles per page)
        pages = []
        page_num = 1
        for i in range(0, len(articles), 2):
            page_articles = articles[i:i+2]
            pages.append({
                'page_num': page_num,
                'articles': page_articles
            })
            page_num += 1
        
        # Prepare template data
        template_data = {
            'title': content_data.get('title', 'الحج والعمرة'),
            'publication_name': content_data.get('publication_name', 'الحج والعمرة'),
            'tagline': content_data.get('tagline', 'مجلة إلكترونية وتعنى بكل ما هو في عالم الحج والعمرة'),
            'issue_number': content_data.get('issue_number', '190'),
            'pages': pages,
            'footer_text': content_data.get('footer_text', 'hajjnews'),
            'contact_phone': content_data.get('contact_phone', '00973 3701 4477'),
            'editors_note': content_data.get('editors_note', ''),
            'cover_image_path': content_data.get('cover_image_path')
        }

        # Optional cover image (look in templates/images)
        # Try Cover.png first, then fallback to other cover images
        cover_path = os.path.join(template_dir, 'images', 'Cover.png')
        if not os.path.exists(cover_path):
            cover_path = os.path.join(template_dir, 'images', '1767655448098.jpg')
        if not os.path.exists(cover_path):
            cover_path = os.path.join(template_dir, 'images', 'cover.png')
        
        if os.path.exists(cover_path):
            template_data['cover_image_path'] = pathlib.Path(cover_path).as_uri()
            logger.info(f"Using cover image: {cover_path}")
        elif content_data.get('cover_image_path'):
            template_data['cover_image_path'] = content_data.get('cover_image_path')
            logger.info(f"Using cover image from content_data")
        else:
            logger.warning("No cover image found")
        
        # Optional back cover image
        back_cover_path = os.path.join(template_dir, 'images', 'Back Cover.png')
        if not os.path.exists(back_cover_path):
            back_cover_path = os.path.join(template_dir, 'images', 'back_cover_generated.png')
        if not os.path.exists(back_cover_path):
            back_cover_path = os.path.join(template_dir, 'images', 'Back Cover.jpg')
        
        if os.path.exists(back_cover_path):
            template_data['back_cover_image_path'] = pathlib.Path(back_cover_path).as_uri()
            logger.info(f"Using back cover image: {back_cover_path}")
        elif content_data.get('back_cover_path'):
            template_data['back_cover_image_path'] = content_data.get('back_cover_path')
            logger.info(f"Using back cover image from content_data")
        
        # Render HTML
        html_out = template.render(**template_data)
        
        # Convert to PDF
        css_path = os.path.join(template_dir, 'newspaper.css')
        HTML(string=html_out, base_url=template_dir).write_pdf(
            output_filename, 
            stylesheets=[CSS(css_path)]
        )
        return output_filename
    except Exception as e:
        logger.error(f"Newspaper PDF rendering error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def render_magazine_pdf(content_data, output_filename="magazine.pdf"):
    """
    Render PDF using Jinja2 and WeasyPrint.
    """
    if not WEASYPRINT_AVAILABLE:
        logger.error("WeasyPrint not available (missing GTK or module). Cannot generate PDF.")
        return None

    try:
        # Setup Jinja2
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template('magazine.html')
        
        # Inject default images if available
        import glob
        import random
        import pathlib
        
        images_dir = os.path.join(template_dir, 'images')
        available_images = []
        if os.path.exists(images_dir):
            all_images = (
                glob.glob(os.path.join(images_dir, '*.jpg')) +
                glob.glob(os.path.join(images_dir, '*.png')) +
                glob.glob(os.path.join(images_dir, '*.jpeg')) +
                glob.glob(os.path.join(images_dir, '*.webp'))
            )
            # Filter out cover images and logo
            exclude_files = ['Cover.png', 'cover.png', 'cover_generated.png', 'back_cover_generated.png', 'Back Cover.png', 'Back Cover.jpg', 'back cover.png', '1767655448098.jpg', 'TransformiX logo .png', 'Logo.jpg']
            available_images = [
                img for img in all_images 
                if os.path.basename(img) not in exclude_files
            ]
        
        # Assign fallback local images to articles that have no real image_url
        if 'articles' in content_data:
            for article in content_data['articles']:
                if available_images and not article.get('image_url') and not article.get('local_image_path'):
                    # Only use a local fallback when no real article image is available
                    img_path = random.choice(available_images)
                    article['local_image_path'] = pathlib.Path(img_path).as_uri()
        
        # Inject Cover Image and Logo
        # Priority: cover.png (User requested)
        cover_path = os.path.join(images_dir, 'cover.png')
        if not os.path.exists(cover_path):
             cover_path = os.path.join(images_dir, 'Cover.png')
        if not os.path.exists(cover_path):
             cover_path = os.path.join(images_dir, 'cover_generated.png')
            
        if os.path.exists(cover_path):
            content_data['cover_image_path'] = pathlib.Path(cover_path).as_uri()
            
        logo_path = os.path.join(images_dir, 'TransformiX logo .png')
        if os.path.exists(logo_path):
            content_data['logo_path'] = pathlib.Path(logo_path).as_uri()

        # Inject Back Cover Image
        # Priority: Back Cover.png (User requested)
        back_cover_path = os.path.join(images_dir, 'Back Cover.png')
        if not os.path.exists(back_cover_path):
             back_cover_path = os.path.join(images_dir, 'back_cover_generated.png')
             
        if os.path.exists(back_cover_path):
            content_data['back_cover_path'] = pathlib.Path(back_cover_path).as_uri()

        # Render HTML
        html_out = template.render(**content_data)
        
        # Convert to PDF
        css_path = os.path.join(template_dir, 'magazine.css')
        HTML(string=html_out, base_url=template_dir).write_pdf(
            output_filename, 
            stylesheets=[CSS(css_path)]
        )
        return output_filename
    except Exception as e:
        logger.error(f"PDF rendering error: {e}")
        return None

def clean_deduplicate_articles(articles):
     # Simple helper if not already present
     seen = set()
     clean = []
     for a in articles:
         t = a.get('title')
         if t and t not in seen:
             seen.add(t)
             clean.append(a)
     return clean

async def generate_magazine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /magazine command."""
    user_id = get_user_id(update)
    
    # Check usage limit
    has_limit, current_usage = check_usage_limit(user_id, 'magazine')
    if not has_limit:
        limit_message = (
            f"❌ *تم الوصول إلى الحد الأقصى*\n\n"
            f"لقد استخدمت جميع المحاولات المتاحة للمجلة ({USAGE_LIMITS['magazine']}/{USAGE_LIMITS['magazine']}).\n\n"
        )
        if update.callback_query:
            await update.callback_query.answer("تم الوصول إلى الحد الأقصى", show_alert=True)
            await update.callback_query.message.reply_text(limit_message, parse_mode='Markdown')
        else:
            await update.message.reply_text(limit_message, parse_mode='Markdown')
        return
    
    # Increment usage
    increment_usage(user_id, 'magazine')
    
    # Send initial status
    if update.callback_query:
        await update.callback_query.answer()
        message = await update.callback_query.message.reply_text(
            "🎨 *مولد مجلة الحج والعمرة*\n\n⏳ جارٍ إعداد الإصدار الموسمي...\n🔍 تحليل أخبار الحج والعمرة للسنة الماضية...",
            parse_mode='Markdown'
        )
    else:
        message = await update.message.reply_text(
            "🎨 *مولد مجلة الحج والعمرة*\n\n⏳ جارٍ إعداد الإصدار الموسمي...\n🔍 تحليل أخبار الحج والعمرة للسنة الماضية...",
            parse_mode='Markdown'
        )

    try:
        # 1. Fetch Monthly News
        await message.edit_text("🎨 *المرحلة 1/3:* جمع المعلومات...", parse_mode='Markdown')
        
        hajgov_articles = fetch_hajgov_news() or []
        cnn_articles = fetch_cnn_hajj_news() or []

        all_articles = clean_deduplicate_articles(hajgov_articles + cnn_articles)
        
        if not all_articles:
             await message.edit_text("❌ لم يتم العثور على بيانات كافية للمجلة.")
             return

        # Enhance top articles
        await message.edit_text("🎨 *المرحلة 2/3:* تنقية وتحسين المحتوى...", parse_mode='Markdown')
        enhanced_articles = enhance_articles_with_content(all_articles, max_articles=30, monthly_mode=True)

        # 2. Generate Content with AI - also returns article_map for direct image lookup
        await message.edit_text("🎨 *المرحلة 3/3:* تصميم التخطيط وإنشاء PDF...", parse_mode='Markdown')
        magazine_data, article_map = generate_magazine_content_with_ai(enhanced_articles)
        
        if not magazine_data:
             await message.edit_text("❌ فشل في توليد محتوى المجلة عبر الذكاء الاصطناعي.")
             return

        # --- Direct image/source back-fill using article_index from AI ---
        mag_articles = magazine_data.get('articles', [])
        for mag_article in mag_articles:
            # Use article_index for direct lookup (most reliable)
            idx_raw = mag_article.get('article_index')
            try:
                idx = int(idx_raw)
            except (TypeError, ValueError):
                idx = None

            orig = article_map.get(idx) if idx else None

            # Back-fill image_url from direct index lookup
            if orig and not mag_article.get('image_url'):
                img = orig.get('image_url', '')
                if img:
                    mag_article['image_url'] = img
                    logger.info(f"Direct image match for '{mag_article.get('title','')[:50]}' via article_index={idx}")
                else:
                    # No image in fields — try to scrape og:image from the article URL
                    article_url = orig.get('url', '')
                    if article_url:
                        og_img = scrape_og_image(article_url)
                        if og_img:
                            mag_article['image_url'] = og_img
                            logger.info(f"OG image scraped for '{mag_article.get('title','')[:50]}': {og_img[:60]}")
                        else:
                            logger.debug(f"No OG image found for '{mag_article.get('title','')[:50]}' - will use render fallback")

        # Add magazine metadata
        current_date = datetime.now()
        magazine_data['date'] = current_date.strftime("%B %Y")
        
        # Ensure all articles have location field (default if missing)
        for article in mag_articles:
            if 'location' not in article or not article['location']:
                article['location'] = 'مكة المكرمة'

        # 3. Render PDF using NEW MAGAZINE template
        filename = f"Hajj_Umrah_{datetime.now().strftime('%B_%Y')}.pdf"
        # SWITCHED from render_newspaper_pdf to render_magazine_pdf
        pdf_path = render_magazine_pdf(magazine_data, filename)
        
        if pdf_path and os.path.exists(pdf_path):
             await message.reply_document(
                document=open(pdf_path, 'rb'),
                filename=filename,
                caption=f"🎨 **مجلة الحج والعمرة - {datetime.now().strftime('%B %Y')}**\n\nاستمتع بتقريرك الموسمي!",
                parse_mode='Markdown'
            )
             # Optional: os.unlink(pdf_path) if running long term
        else:
             await message.edit_text("❌ فشل في إنشاء ملف PDF.")

    except Exception as e:
        logger.error(f"Magazine error: {e}")
        await message.edit_text(f"❌ خطأ: {str(e)}")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset usage limits for users (admin only)."""
    user_id = get_user_id(update)
    
    # Check if user is admin
    if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
        await update.message.reply_text(
            "❌ *غير مصرح*\n\nهذا الأمر متاح للمسؤولين فقط.",
            parse_mode='Markdown'
        )
        return
    
    # Check if specific user ID provided
    if context.args and len(context.args) > 0:
        try:
            target_user_id = int(context.args[0])
            if reset_user_usage(target_user_id):
                await update.message.reply_text(
                    f"✅ تم إعادة تعيين المحاولات للمستخدم: {target_user_id}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    f"❌ لم يتم العثور على المستخدم: {target_user_id}",
                    parse_mode='Markdown'
                )
        except ValueError:
            await update.message.reply_text(
                "❌ معرّف المستخدم غير صحيح. استخدم: `/reset [user_id]` أو `/reset all`",
                parse_mode='Markdown'
            )
    elif context.args and context.args[0].lower() == 'all':
        reset_user_usage()
        await update.message.reply_text(
            "✅ تم إعادة تعيين جميع المحاولات لجميع المستخدمين.",
            parse_mode='Markdown'
        )
    else:
        # Reset current user
        reset_user_usage(user_id)
        await update.message.reply_text(
            "✅ تم إعادة تعيين محاولاتك.",
            parse_mode='Markdown'
        )

async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current usage status."""
    user_id = get_user_id(update)
    status = get_usage_status(user_id)
    
    status_message = (
        "📊 *حالة الاستخدام الحالية*\n\n"
        f"📰 الأخبار اليومية: {status['daily_news']['used']}/{status['daily_news']['limit']}\n"
        f"📝 التقارير الأسبوعية: {status['weekly']['used']}/{status['weekly']['limit']}\n"
        f"📅 التقارير الشهرية: {status['monthly']['used']}/{status['monthly']['limit']}\n"
        f"🎨 المجلة: {status['magazine']['used']}/{status['magazine']['limit']}\n\n"
        f"استخدم `/reset` لإعادة تعيين المحاولات (للمسؤولين فقط)."
    )
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = """
⭐ *مساعدة بوت أخبار الحج والعمرة المحسّنة*

*🆕 المزايا المحسّنة:*
• 📖 **استخراج كامل للمقالات** – قراءة النص الكامل للمقالات وليس الوصف فقط  
• 🧠 **ملخصات أذكى** – تحليل يعتمد على المحتوى الكامل  
• 📝 **توليد مدونات أسبوعية وشهرية** – تقارير معمّقة عن خدمات الحج والعمرة  
• 🎨 **توليد مجلة الحج والعمرة الشهرية** – مجلة PDF احترافية بتصميم جميل
• 🔍 **استخراج متعدد الأساليب** – استخدام newspaper3k و BeautifulSoup  
• 📊 **إحصائيات المحتوى** – عرض نسبة نجاح استخراج النصوص  
• 🎯 **فلترة موجهة للحج والعمرة** – استبعاد الأخبار الرياضية والجرائم وغير ذات الصلة

*الأوامر المتاحة:*
• `/start` – رسالة الترحيب والقائمة الرئيسية  
• `/news` – الحصول على أخبار الحج والعمرة المحسّنة مع المحتوى الكامل  
• `/categories` – تصفح الأخبار حسب التصنيف  
• `/weekly` – توليد تقارير/مدونات أسبوعية شاملة  
• `/monthly` – توليد تقارير/مدونات شهرية شاملة  
• `/magazine` – توليد مجلة الحج والعمرة الشهرية احترافية (PDF)
• `/keywords` – إعداد الكلمات المفتاحية الأساسية والثانوية (بالإنجليزية) لتحسين محركات البحث  
• `/help` – عرض رسالة المساعدة هذه

*كيف يعمل الاستخراج المحسّن للمحتوى:*
1. 📡 جلب الأخبار من NewsAPI و GNews وتغذيات RSS المتخصصة  
2. 🔍 استخراج المحتوى الكامل من الروابط  
3. 📖 استخدام طريقتَي newspaper3k و BeautifulSoup  
4. 🧠 تطبيق فلترة موجهة للحج والعمرة لإزالة الضجيج  
5. 📄 إنشاء تقارير تفصيلية وملفات PDF

*التصنيفات المتاحة:*
• 📊 خدمات الحجاج  
• 🏆 معايير ISO والشهادات  
• ⭐ أطر التميز والجوائز  
• 🔄 تحسين العمليات واللين  
• 📰 أخبار عامة  

*فوائد استخدام المحتوى الكامل:*
• ملخصات أكثر دقة  
• تصنيف أفضل للمقالات  
• رؤى وتحليلات أعمق  
• فهم كامل للسياق  
• تقارير احترافية قابلة للمشاركة  
• دعم توليد مدونات أسبوعية وشهرية

استخدم `/news` للتحديثات اليومية، و`/weekly` للتقارير الأسبوعية، و`/monthly` للتقارير الشهرية، و`/magazine` للمجلة الشهرية الاحترافية.
    """
    
    # Handle both regular commands and callback queries
    if update.callback_query:
        await update.callback_query.message.reply_text(help_text, parse_mode='Markdown')
    else:
        await update.message.reply_text(help_text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    welcome_message = """
⭐ 👋 مرحباً بك! أنا مساعدك الإخباري الذكي لقطاع الحج والعمرة
تم تصميمي خصيصاً لأكون رفيقك اليومي في متابعة كل ما يخص أخبار وخدمات الحج والعمرة.
أقوم بجمع أحدث المستجدات، تحليلها، وتلخيصها لك بدقة واحترافية عالية،
لتكون دائماً في قلب الحدث دون إهدار وقتك في البحث بين المصادر المتعددة.

🤖 ملاحظة هامة:
أعتمد على خوارزميات الذكاء الاصطناعي المتقدمة لمعالجة وتلخيص الأخبار.
(هذه الخدمة تهدف لتسهيل المتابعة ولا تعتبر بديلاً عن التصريحات والقرارات الرسمية).

✨ أبرز ما أوفره لك:
📰 ملخصات يومية لأهم وأحدث أخبار القطاع.
📊 تقارير تحليلية شاملة ومفصلة (أسبوعية وشهرية).
📘 إصدارات شهرية متكاملة بصيغة PDF جاهزة للمشاركة.
⏱️ توفير الجهد والوقت لتبقَ مطلعاً على مدار الساعة.

🎯 لماذا تحتاجني؟
• لتكون على دراية تامة بمتغيرات السوق بشكل فوري.
• لتزويد فريق عملك وعملائك بتقارير دورية احترافية وموثوقة.
• لدعم اجتماعاتك الإدارية بملخصات دقيقة جاهزة للاستخدام.

🚀 جاهز للبدء؟
استخدم الخيارات والأزرار بالأسفل لاستكشاف الأخبار والتقارير.
    """

    
    keyboard = [
        [InlineKeyboardButton("📰 الملخص اليومي", callback_data='get_news')],
        [InlineKeyboardButton("📊 الملخص الأسبوعي", callback_data='generate_weekly'),
         InlineKeyboardButton("📅 الملخص الشهري", callback_data='generate_monthly')],
        [InlineKeyboardButton("📰 المجلة", callback_data='generate_magazine')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Handle both regular messages and callback queries
    if update.callback_query:
        await update.callback_query.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

def main():
    """Start the Hajj and Umrah news bot."""
    # Create the Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("news", get_news))
    application.add_handler(CommandHandler("categories", show_categories))
    application.add_handler(CommandHandler("weekly", weekly_command))  # Weekly blog command
    application.add_handler(CommandHandler("monthly", monthly_command))  # Monthly blog command
    application.add_handler(CommandHandler("magazine", generate_magazine))  # Magazine command
    application.add_handler(CommandHandler("keywords", keywords_command))
    application.add_handler(CommandHandler("setkeywords", keywords_command))
    application.add_handler(CommandHandler("reset", reset_command))  # Reset usage command
    application.add_handler(CommandHandler("usage", usage_command))  # Show usage status
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the bot
    print("⭐ Starting Enhanced Hajj and Umrah News Bot...")
    print("📱 Bot is ready! Send /start to begin.")
    print("✨ Enhanced features:")
    print("   • 📖 Full article content extraction")
    print("   • 🧠 Hajj and Umrah-specific filtering")
    print("   • 📝 Weekly & monthly blog generation")
    print("   • 📄 Enhanced reports with full content")
    print("   • 🔍 Multi-method content extraction")
    print("   • 📊 Content extraction statistics")
    print("   • ⚡ Smart categorization using full text")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
