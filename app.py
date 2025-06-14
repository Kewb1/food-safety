import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import time
import urllib.parse

# Initialize Flask app
app = Flask(__name__)

# Configure CORS for production
CORS(app, origins=["*"])

# Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'fallback-secret-key-for-development'
    FDA_API_BASE = "https://api.fda.gov/food/enforcement.json"
    CPSC_API_BASE = "http://www.saferproducts.gov/RestWebServices/Recall"
    CACHE_DURATION = 3600  # 1 hour in seconds

app.config.from_object(Config)

# Simple in-memory cache (no database needed for Railway)
cache = {
    'fda_recalls': {'data': None, 'timestamp': 0},
    'cpsc_recalls': {'data': None, 'timestamp': 0},
    'stats': {'data': None, 'timestamp': 0}
}

def is_cache_valid(cache_key: str) -> bool:
    """Check if cached data is still valid"""
    if cache_key not in cache:
        return False
    cache_age = time.time() - cache[cache_key]['timestamp']
    return cache_age < Config.CACHE_DURATION

def get_cached_data(cache_key: str):
    """Get cached data if valid"""
    if is_cache_valid(cache_key):
        return cache[cache_key]['data']
    return None

def set_cache_data(cache_key: str, data):
    """Set data in cache with current timestamp"""
    cache[cache_key] = {
        'data': data,
        'timestamp': time.time()
    }

def fetch_fda_recalls_with_search(search_query: str = None) -> List[Dict]:
    """Fetch food recalls from FDA API with optional search query"""
    try:
        print(f"Fetching FDA recalls from: {Config.FDA_API_BASE}")
        params = {'limit': 1000}  # Get maximum available from API
        
        # Add search query to FDA API if provided
        if search_query:
            # FDA API uses simple search - just the term without field specification
            params['search'] = search_query
            print(f"FDA search query: {params['search']}")
        
        response = requests.get(Config.FDA_API_BASE, params=params, timeout=30)
        
        # If search fails with specific term, try broader approaches
        if response.status_code != 200 and search_query:
            print(f"FDA search failed (status {response.status_code}), trying fallback approaches...")
            
            # Try without search parameter - get all data and filter locally
            params_fallback = {'limit': 1000}
            response = requests.get(Config.FDA_API_BASE, params=params_fallback, timeout=30)
            
            if response.status_code == 200:
                print("FDA API accessible, will filter results locally")
            else:
                print(f"FDA API completely inaccessible: {response.status_code}")
                return []
        
        response.raise_for_status()
        
        data = response.json()
        recalls = data.get('results', [])
        
        # If we fell back to getting all data, filter locally
        if search_query and (response.url.find('search=') == -1 or len(recalls) == 0):
            print(f"Filtering {len(recalls)} FDA recalls locally for: {search_query}")
            filtered_recalls = []
            search_lower = search_query.lower()
            for recall in recalls:
                # Search in key fields
                searchable_text = ' '.join([
                    recall.get('product_description', ''),
                    recall.get('reason_for_recall', ''),
                    recall.get('recalling_firm', '')
                ]).lower()
                
                if search_lower in searchable_text:
                    filtered_recalls.append(recall)
            
            recalls = filtered_recalls
            print(f"Local filtering found {len(recalls)} matching FDA recalls")
        
        # Process and clean the data
        processed_recalls = []
        for recall in recalls:
            processed_recall = {
                'id': recall.get('recall_number', f"FDA-{len(processed_recalls)}"),
                'recall_number': recall.get('recall_number', 'N/A'),
                'product_description': recall.get('product_description', 'N/A'),
                'reason_for_recall': recall.get('reason_for_recall', 'N/A'),
                'company': recall.get('recalling_firm', 'N/A'),
                'date': recall.get('recall_initiation_date', 'N/A'),
                'classification': recall.get('classification', 'N/A'),
                'status': recall.get('status', 'N/A'),
                'distribution_pattern': recall.get('distribution_pattern', 'N/A'),
                'product_quantity': recall.get('product_quantity', 'N/A'),
                'source': 'FDA'
            }
            processed_recalls.append(processed_recall)
        
        print(f"Retrieved {len(processed_recalls)} FDA recalls")
        return processed_recalls
        
    except requests.RequestException as e:
        print(f"Error fetching FDA data: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error fetching FDA data: {e}")
        return []

def fetch_fda_recalls() -> List[Dict]:
    """Fetch ALL food recalls from FDA API (for caching)"""
    try:
        # Check cache first
        cached_data = get_cached_data('fda_recalls')
        if cached_data:
            return cached_data
        
        # Fetch without search query for caching
        recalls = fetch_fda_recalls_with_search()
        
        # Cache the full processed data
        set_cache_data('fda_recalls', recalls)
        print(f"Retrieved and cached {len(recalls)} FDA recalls")
        
        return recalls
        
    except Exception as e:
        print(f"Unexpected error in fetch_fda_recalls: {e}")
        return []

def fetch_cpsc_recalls_with_search(search_query: str = None) -> List[Dict]:
    """Fetch CPSC consumer product recalls with optional search"""
    try:
        print(f"Fetching CPSC recalls from: {Config.CPSC_API_BASE}")
        
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'FoodSafetyMonitor/1.0 (Contact: your-email@domain.com)'
        }
        
        params = {
            'format': 'json',
            'RecallDateStart': '2024-01-01'  # Get dataset from 2024
        }
        
        # Add search parameters to CPSC API if provided
        if search_query:
            # CPSC API supports multiple search fields - try ProductName first
            params['ProductName'] = search_query
            print(f"CPSC search query: ProductName={search_query}")
        
        recall_delimited_url = Config.CPSC_API_BASE.replace('/Recall', '/RecallDelimited')
        
        try:
            response = requests.get(
                recall_delimited_url, 
                headers=headers, 
                params=params,
                timeout=45
            )
            
            print(f"CPSC API Response Status: {response.status_code}")
            
            # If ProductName search returns no results, try without search to get all data
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"CPSC API returned {len(data) if isinstance(data, list) else 'unknown'} records")
                    
                    # If we got no results with search, try getting all data and filter locally
                    if search_query and (not isinstance(data, list) or len(data) == 0):
                        print("CPSC search returned no results, trying to get all data and filter locally...")
                        params_no_search = {
                            'format': 'json',
                            'RecallDateStart': '2024-01-01'
                        }
                        
                        response_all = requests.get(
                            recall_delimited_url,
                            headers=headers,
                            params=params_no_search,
                            timeout=45
                        )
                        
                        if response_all.status_code == 200:
                            all_data = response_all.json()
                            if isinstance(all_data, list) and len(all_data) > 0:
                                print(f"Got {len(all_data)} total CPSC records, filtering locally...")
                                
                                # Filter locally
                                search_lower = search_query.lower()
                                filtered_data = []
                                
                                for recall in all_data:
                                    # Search in multiple fields
                                    searchable_text = ' '.join([
                                        recall.get('ProductNames', ''),
                                        recall.get('ProductDescriptions', ''),
                                        recall.get('Title', ''),
                                        recall.get('Manufacturers', ''),
                                        recall.get('Hazards', '')
                                    ]).lower()
                                    
                                    if search_lower in searchable_text:
                                        filtered_data.append(recall)
                                
                                data = filtered_data
                                print(f"Local filtering found {len(data)} matching CPSC recalls")
                    
                    if isinstance(data, list) and len(data) > 0:
                        # Normalize the data
                        normalized_recalls = normalize_cpsc_recalls(data)
                        print(f"Retrieved {len(normalized_recalls)} CPSC recalls")
                        
                        return normalized_recalls
                    else:
                        print("CPSC API returned empty or invalid data")
                        
                except json.JSONDecodeError as e:
                    print(f"Error parsing CPSC JSON response: {e}")
                    print(f"Response content preview: {response.text[:500]}")
            else:
                print(f"CPSC API returned status {response.status_code}: {response.text[:200]}")
                
        except requests.Timeout:
            print("CPSC API request timed out")
        except requests.ConnectionError:
            print("CPSC API connection error")
        except requests.RequestException as e:
            print(f"CPSC API request error: {e}")
        
        return []
        
    except Exception as e:
        print(f"Unexpected error in fetch_cpsc_recalls_with_search: {e}")
        return []

def fetch_cpsc_recalls() -> List[Dict]:
    """Fetch ALL CPSC consumer product recalls (for caching)"""
    try:
        # Check cache first
        cached_data = get_cached_data('cpsc_recalls')
        if cached_data:
            return cached_data
        
        # Fetch without search query for caching
        recalls = fetch_cpsc_recalls_with_search()
        
        # Cache the full normalized data
        set_cache_data('cpsc_recalls', recalls)
        print(f"Retrieved and cached {len(recalls)} CPSC recalls")
        
        return recalls
        
    except Exception as e:
        print(f"Unexpected error in fetch_cpsc_recalls: {e}")
        empty_data = []
        set_cache_data('cpsc_recalls', empty_data)
        return empty_data

def normalize_cpsc_recalls(raw_recalls: List[Dict]) -> List[Dict]:
    """Normalize CPSC API response to standard format using RecallDelimited fields"""
    normalized = []
    
    for i, recall in enumerate(raw_recalls):
        try:
            if not isinstance(recall, dict):
                continue
            
            # Use the correct field names from RecallDelimited documentation
            recall_number = recall.get('RecallNumber', f"CPSC-{i+1:03d}")
            recall_id = recall.get('RecallID', recall_number)
            
            # Product information - use ProductNames and ProductDescriptions
            product_names = recall.get('ProductNames', '')
            product_descriptions = recall.get('ProductDescriptions', '')
            title = recall.get('Title', '')
            
            # Combine product info for better description
            product_description_parts = []
            if title:
                product_description_parts.append(title)
            if product_names:
                product_description_parts.append(product_names)
            if product_descriptions:
                product_description_parts.append(product_descriptions)
            
            product_description = ' - '.join(filter(None, product_description_parts)) or 'Consumer Product'
            
            # Hazard information - use Hazards field
            hazards = recall.get('Hazards', '')
            injuries = recall.get('Injuries', '')
            description = recall.get('Description', '')
            
            # Combine hazard info for reason_for_recall
            reason_parts = []
            if hazards:
                reason_parts.append(f"Hazard: {hazards}")
            if injuries:
                reason_parts.append(f"Injuries: {injuries}")
            if description and not hazards and not injuries:
                reason_parts.append(description)
            
            reason_for_recall = ' | '.join(filter(None, reason_parts)) or 'See CPSC for details'
            
            # Manufacturer information
            manufacturers = recall.get('Manufacturers', 'Unknown Manufacturer')
            manufacturer_countries = recall.get('ManufacturerCountries', '')
            
            # Combine manufacturer info
            if manufacturers and manufacturer_countries:
                company = f"{manufacturers}, of {manufacturer_countries}"
            elif manufacturers:
                company = manufacturers
            else:
                company = 'Unknown Manufacturer'
            
            # Date formatting
            date = recall.get('RecallDate', '20240101')
            if 'T' in str(date):
                try:
                    dt = datetime.fromisoformat(date.replace('T', ' ').replace('Z', ''))
                    date = dt.strftime('%Y%m%d')
                except:
                    date = '20240101'
            
            # Product quantity
            number_of_units = recall.get('NumberOfUnits', 'See CPSC for details')
            
            normalized_recall = {
                'id': str(recall_id),
                'recall_number': recall_number,
                'product_description': product_description,
                'reason_for_recall': reason_for_recall,
                'company': company,
                'date': date,
                'classification': 'Consumer Product',
                'status': 'Active',
                'distribution_pattern': 'See CPSC for details',
                'product_quantity': number_of_units,
                'source': 'CPSC'
            }
            
            normalized.append(normalized_recall)
            
        except Exception as e:
            print(f"Error normalizing CPSC recall {i}: {e}")
            continue
    
    return normalized
  
def generate_stats(fda_recalls: List[Dict], cpsc_recalls: List[Dict]) -> Dict:
    """Generate statistics from recalls data"""
    all_recalls = fda_recalls + cpsc_recalls
    
    if not all_recalls:
        return {
            'total_recalls': 0,
            'fda_recalls': 0,
            'cpsc_recalls': 0,
            'recent_recalls': 0,
            'classifications': {},
            'top_reasons': []
        }
    
    # Count recent recalls (last 30 days)
    recent_count = 0
    current_date = datetime.now()
    thirty_days_ago = current_date - timedelta(days=30)
    
    reason_counts = {}
    classification_counts = {}
    
    for recall in all_recalls:
        # Count reasons
        reason = recall.get('reason_for_recall', 'Unknown')
        if reason and reason != 'N/A':
            reason_short = reason[:50] + '...' if len(reason) > 50 else reason
            reason_counts[reason_short] = reason_counts.get(reason_short, 0) + 1
        
        # Count classifications
        classification = recall.get('classification', 'Unknown')
        if classification and classification != 'N/A':
            classification_counts[classification] = classification_counts.get(classification, 0) + 1
        
        # Count recent recalls
        try:
            recall_date_str = recall.get('date', '')
            if recall_date_str and recall_date_str != 'N/A':
                if len(recall_date_str) == 8:  # YYYYMMDD format
                    date = datetime.strptime(recall_date_str, '%Y%m%d')
                    if date >= thirty_days_ago:
                        recent_count += 1
        except (ValueError, TypeError):
            continue
    
    # Get top 5 reasons
    top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return {
        'total_recalls': len(all_recalls),
        'fda_recalls': len(fda_recalls),
        'cpsc_recalls': len(cpsc_recalls),
        'recent_recalls': recent_count,
        'classifications': classification_counts,
        'top_reasons': [{'reason': reason, 'count': count} for reason, count in top_reasons]
    }

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'Food Safety Monitor API is running',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.1',
        'environment': os.environ.get('FLASK_ENV', 'development')
    })

@app.route('/api/recalls')
def get_recalls():
    """Get food recalls with optional filtering"""
    try:
        # Get query parameters for filtering
        search = request.args.get('search', '').strip()
        classification = request.args.get('classification', '')
        source = request.args.get('source', '')
        
        all_recalls = []
        fda_recalls = []
        cpsc_recalls = []
        
        # If there's a search query, search the APIs directly
        if search:
            print(f"Performing API search for: '{search}'")
            
            # Search both APIs directly if no source specified
            if not source or source.lower() == 'fda':
                fda_recalls = fetch_fda_recalls_with_search(search)
                all_recalls.extend(fda_recalls)
                
            if not source or source.lower() == 'cpsc':
                cpsc_recalls = fetch_cpsc_recalls_with_search(search)
                all_recalls.extend(cpsc_recalls)
        else:
            # No search query - get cached data or fetch all
            if not source or source.lower() == 'fda':
                fda_recalls = fetch_fda_recalls()
                all_recalls.extend(fda_recalls)
                
            if not source or source.lower() == 'cpsc':
                cpsc_recalls = fetch_cpsc_recalls()
                all_recalls.extend(cpsc_recalls)
        
        print(f"DEBUG: FDA recalls fetched: {len(fda_recalls)}")
        print(f"DEBUG: CPSC recalls fetched: {len(cpsc_recalls)}")
        print(f"DEBUG: Total recalls before additional filtering: {len(all_recalls)}")
        
        # Apply additional filters (classification, source) to results
        filtered_recalls = all_recalls
        
        if classification:
            filtered_recalls = [
                recall for recall in filtered_recalls
                if recall.get('classification', '').lower() == classification.lower()
            ]
        
        # Apply source filter if specified (this is redundant with the above logic but kept for safety)
        if source:
            filtered_recalls = [
                recall for recall in filtered_recalls
                if recall.get('source', '').lower() == source.lower()
            ]
        
        print(f"DEBUG: Final filtered recalls: {len(filtered_recalls)}")
        
        return jsonify({
            'success': True,
            'data': filtered_recalls,
            'count': len(filtered_recalls),
            'total_available': len(filtered_recalls),
            'search_performed': bool(search),
            'filters': {
                'search': search,
                'classification': classification,
                'source': source
            },
            'sources': {
                'fda_count': len(fda_recalls),
                'cpsc_count': len(cpsc_recalls)
            }
        })
        
    except Exception as e:
        print(f"Error in get_recalls: {e}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/stats')
def get_stats():
    """Get recall statistics"""
    try:
        # Check cache first
        cached_stats = get_cached_data('stats')
        if cached_stats:
            return jsonify({
                'success': True,
                'data': cached_stats,
                'cached': True
            })
        
        # Fetch fresh data (all of it)
        fda_recalls = fetch_fda_recalls()
        cpsc_recalls = fetch_cpsc_recalls()
        stats = generate_stats(fda_recalls, cpsc_recalls)
        
        # Cache the stats
        set_cache_data('stats', stats)
        
        return jsonify({
            'success': True,
            'data': stats,
            'cached': False
        })
        
    except Exception as e:
        print(f"Error in get_stats: {e}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/search')
def search_recalls():
    """Search recalls by keyword using external APIs"""
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({
                'success': False,
                'error': 'Search query is required'
            }), 400
        
        source = request.args.get('source', '')
        
        print(f"Performing direct API search for: '{query}'")
        
        all_recalls = []
        fda_recalls = []
        cpsc_recalls = []
        
        # Search both APIs directly with the query
        if not source or source.lower() == 'fda':
            fda_recalls = fetch_fda_recalls_with_search(query)
            all_recalls.extend(fda_recalls)
            
        if not source or source.lower() == 'cpsc':
            cpsc_recalls = fetch_cpsc_recalls_with_search(query)
            all_recalls.extend(cpsc_recalls)
        
        return jsonify({
            'success': True,
            'data': all_recalls,
            'count': len(all_recalls),
            'query': query,
            'api_search': True,
            'sources': {
                'fda_count': len(fda_recalls),
                'cpsc_count': len(cpsc_recalls)
            }
        })
        
    except Exception as e:
        print(f"Error in search_recalls: {e}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/test')
def test_api():
    """Test endpoint to check API connectivity"""
    try:
        # Test FDA API
        fda_response = requests.get(f"{Config.FDA_API_BASE}?limit=1", timeout=10)
        fda_status = fda_response.status_code == 200
        
        # Test CPSC API
        cpsc_status = False
        cpsc_status_code = None
        try:
            cpsc_response = requests.get(
                f"{Config.CPSC_API_BASE}?format=json&RecallDateStart=2024-01-01", 
                timeout=15
            )
            cpsc_status = cpsc_response.status_code == 200
            cpsc_status_code = cpsc_response.status_code
        except Exception as e:
            cpsc_status_code = f"Error: {str(e)}"
        
        return jsonify({
            'success': True,
            'api_tests': {
                'fda_api': {
                    'status': 'working' if fda_status else 'failed',
                    'status_code': fda_response.status_code
                },
                'cpsc_api': {
                    'status': 'working' if cpsc_status else 'failed',
                    'status_code': cpsc_status_code
                }
            },
            'cache_status': {
                'fda_cached': is_cache_valid('fda_recalls'),
                'cpsc_cached': is_cache_valid('cpsc_recalls'),
                'stats_cached': is_cache_valid('stats')
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/update')
def update_data():
    """Force refresh cached data"""
    try:
        # Clear cache
        cache.clear()
        
        # Fetch fresh data (this will populate cache)
        fda_recalls = fetch_fda_recalls()
        cpsc_recalls = fetch_cpsc_recalls()
        
        return jsonify({
            'success': True,
            'message': 'Data updated successfully',
            'counts': {
                'fda_recalls': len(fda_recalls),
                'cpsc_recalls': len(cpsc_recalls)
            }
        })
    except Exception as e:
        print(f"Error in update_data: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to update data'
        }), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': 'Endpoint not found'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': 'Internal server error'
    }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    print(f"Starting Food Safety Monitor API on port {port}")
    print(f"Environment: {os.environ.get('FLASK_ENV', 'development')}")
    print(f"Debug mode: {debug}")
    
    app.run(host='0.0.0.0', port=port, debug=debug)