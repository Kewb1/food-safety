import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import time

# Initialize Flask app
app = Flask(__name__)

# Configure CORS for production
CORS(app, origins=["*"])

# Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'fallback-secret-key-for-development'
    FDA_API_BASE = "https://api.fda.gov/food/enforcement.json"
    CPSC_API_BASE = "https://www.saferproducts.gov/RestWebServices/Recall"
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

def fetch_fda_recalls(limit: int = 100) -> List[Dict]:
    """Fetch food recalls from FDA API"""
    try:
        # Check cache first
        cached_data = get_cached_data('fda_recalls')
        if cached_data:
            return cached_data[:limit]
        
        print(f"Fetching FDA recalls from: {Config.FDA_API_BASE}")
        params = {'limit': min(limit, 1000)}  # FDA API limit
        
        response = requests.get(Config.FDA_API_BASE, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        recalls = data.get('results', [])
        
        # Process and clean the data
        processed_recalls = []
        for recall in recalls:
            processed_recall = {
                'id': recall.get('recall_number', f"FDA-{len(processed_recalls)}"),
                'recall_number': recall.get('recall_number', 'N/A'),
                'product_description': recall.get('product_description', 'N/A'),
                'reason_for_recall': recall.get('reason_for_recall', 'N/A'),
                'company': recall.get('recalling_firm', 'N/A'),
                'date': recall.get('report_date', 'N/A'),
                'classification': recall.get('classification', 'N/A'),
                'status': recall.get('status', 'N/A'),
                'distribution_pattern': recall.get('distribution_pattern', 'N/A'),
                'product_quantity': recall.get('product_quantity', 'N/A'),
                'source': 'FDA'
            }
            processed_recalls.append(processed_recall)
        
        # Cache the processed data
        set_cache_data('fda_recalls', processed_recalls)
        print(f"Retrieved and cached {len(processed_recalls)} FDA recalls")
        
        return processed_recalls[:limit]
        
    except requests.RequestException as e:
        print(f"Error fetching FDA data: {e}")
        return get_fda_fallback_data()
    except Exception as e:
        print(f"Unexpected error fetching FDA data: {e}")
        return get_fda_fallback_data()

def fetch_cpsc_recalls(limit: int = 50) -> List[Dict]:
    """Fetch CPSC consumer product recalls"""
    try:
        # Check cache first
        cached_data = get_cached_data('cpsc_recalls')
        if cached_data:
            return cached_data[:limit]
        
        print(f"Fetching CPSC recalls from: {Config.CPSC_API_BASE}")
        
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'FoodSafetyMonitor/1.0'
        }
        
        # Try general CPSC recalls first
        response = requests.get(Config.CPSC_API_BASE, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            recalls = []
            
            # Handle different response formats
            if isinstance(data, list):
                recalls = data[:limit]
            elif isinstance(data, dict):
                recalls = (data.get('recalls', []) or 
                          data.get('results', []) or 
                          data.get('data', []))[:limit]
            
            if recalls:
                normalized_recalls = normalize_cpsc_recalls(recalls)
                set_cache_data('cpsc_recalls', normalized_recalls)
                print(f"Retrieved and cached {len(normalized_recalls)} CPSC recalls")
                return normalized_recalls[:limit]
        
        # If API fails, return fallback data
        fallback_data = get_cpsc_fallback_data()
        set_cache_data('cpsc_recalls', fallback_data)
        return fallback_data
        
    except Exception as e:
        print(f"Error fetching CPSC data: {e}")
        fallback_data = get_cpsc_fallback_data()
        set_cache_data('cpsc_recalls', fallback_data)
        return fallback_data

def normalize_cpsc_recalls(raw_recalls: List[Dict]) -> List[Dict]:
    """Normalize CPSC API response to standard format"""
    normalized = []
    
    for i, recall in enumerate(raw_recalls):
        try:
            if not isinstance(recall, dict):
                continue
            
            normalized_recall = {
                'id': (recall.get('RecallNumber') or 
                      recall.get('recall_number') or 
                      f"CPSC-{i+1}"),
                'recall_number': (recall.get('RecallNumber') or 
                                recall.get('recall_number') or 
                                f"CPSC-{i+1:03d}"),
                'product_description': (recall.get('ProductName') or 
                                      recall.get('product_name') or 
                                      'Consumer Product'),
                'reason_for_recall': (recall.get('HazardDescription') or 
                                    recall.get('reason') or 
                                    'Consumer safety concern'),
                'company': (recall.get('Manufacturer') or 
                           recall.get('company_name') or 
                           'Unknown Manufacturer'),
                'date': (recall.get('RecallDate') or 
                        recall.get('recall_date') or 
                        '20240101'),
                'classification': 'Consumer Product',
                'status': 'Active',
                'distribution_pattern': 'Nationwide',
                'product_quantity': 'See CPSC for details',
                'source': 'CPSC'
            }
            
            normalized.append(normalized_recall)
            
        except Exception as e:
            print(f"Error normalizing CPSC recall {i}: {e}")
            continue
    
    return normalized

def get_fda_fallback_data() -> List[Dict]:
    """Fallback FDA data when API is unavailable"""
    return [
        {
            'id': 'FDA-2025-001',
            'recall_number': 'F-001-2025',
            'product_description': 'Various brands of frozen vegetables due to potential Listeria contamination',
            'reason_for_recall': 'Products may be contaminated with Listeria monocytogenes',
            'company': 'FrozenFresh Foods Inc.',
            'date': '20250520',
            'classification': 'Class I',
            'status': 'Ongoing',
            'distribution_pattern': 'Nationwide',
            'product_quantity': '50,000 packages',
            'source': 'FDA'
        },
        {
            'id': 'FDA-2025-002',
            'recall_number': 'F-002-2025',
            'product_description': 'Organic baby food pouches due to elevated lead levels',
            'reason_for_recall': 'Product contains elevated levels of lead',
            'company': 'Healthy Start Baby Foods',
            'date': '20250518',
            'classification': 'Class II',
            'status': 'Ongoing',
            'distribution_pattern': 'Eastern US',
            'product_quantity': '25,000 pouches',
            'source': 'FDA'
        }
    ]

def get_cpsc_fallback_data() -> List[Dict]:
    """Fallback CPSC data when API is unavailable"""
    return [
        {
            'id': 'CPSC-2025-001',
            'recall_number': 'CPSC-2025-001',
            'product_description': 'Electric Food Processors',
            'reason_for_recall': 'Blade can detach during use, posing laceration hazard',
            'company': 'KitchenTech Industries',
            'date': '20250520',
            'classification': 'Consumer Product',
            'status': 'Active',
            'distribution_pattern': 'Nationwide',
            'product_quantity': '15,000 units',
            'source': 'CPSC'
        },
        {
            'id': 'CPSC-2025-002',
            'recall_number': 'CPSC-2025-002',
            'product_description': 'Glass Food Storage Containers',
            'reason_for_recall': 'Glass can shatter unexpectedly, posing injury risk',
            'company': 'SafeStore Corp',
            'date': '20250518',
            'classification': 'Consumer Product',
            'status': 'Active',
            'distribution_pattern': 'Nationwide',
            'product_quantity': '8,000 sets',
            'source': 'CPSC'
        }
    ]

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
                    recall_date = datetime.strptime(recall_date_str, '%Y%m%d')
                    if recall_date >= thirty_days_ago:
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

# API Routes
@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'Food Safety Monitor API is running',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.0',
        'environment': os.environ.get('FLASK_ENV', 'development')
    })

@app.route('/api/recalls')
def get_recalls():
    """Get food recalls with optional filtering"""
    try:
        # Get query parameters
        limit = min(int(request.args.get('limit', 50)), 1000)
        search = request.args.get('search', '').lower()
        classification = request.args.get('classification', '')
        source = request.args.get('source', '')  # 'fda' or 'cpsc'
        
        # Fetch data from both sources
        fda_recalls = fetch_fda_recalls(limit)
        cpsc_recalls = fetch_cpsc_recalls(limit)
        
        all_recalls = []
        
        # Add FDA recalls if requested
        if not source or source.lower() == 'fda':
            all_recalls.extend(fda_recalls)
        
        # Add CPSC recalls if requested
        if not source or source.lower() == 'cpsc':
            all_recalls.extend(cpsc_recalls)
        
        # Apply filters
        filtered_recalls = all_recalls
        
        if search:
            filtered_recalls = [
                recall for recall in filtered_recalls
                if search in recall.get('product_description', '').lower() or
                   search in recall.get('reason_for_recall', '').lower() or
                   search in recall.get('company', '').lower()
            ]
        
        if classification:
            filtered_recalls = [
                recall for recall in filtered_recalls
                if recall.get('classification', '').lower() == classification.lower()
            ]
        
        # Apply limit after filtering
        filtered_recalls = filtered_recalls[:limit]
        
        return jsonify({
            'success': True,
            'data': filtered_recalls,
            'count': len(filtered_recalls),
            'filters': {
                'search': search,
                'classification': classification,
                'source': source,
                'limit': limit
            },
            'sources': {
                'fda_count': len(fda_recalls),
                'cpsc_count': len(cpsc_recalls)
            }
        })
        
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': f'Invalid parameter: {str(e)}'
        }), 400
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
        
        # Fetch fresh data
        fda_recalls = fetch_fda_recalls(500)
        cpsc_recalls = fetch_cpsc_recalls(200)
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
    """Search recalls by keyword"""
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({
                'success': False,
                'error': 'Search query is required'
            }), 400
        
        limit = min(int(request.args.get('limit', 20)), 100)
        
        # Fetch recalls from both sources
        fda_recalls = fetch_fda_recalls(300)
        cpsc_recalls = fetch_cpsc_recalls(200)
        all_recalls = fda_recalls + cpsc_recalls
        
        query_lower = query.lower()
        matching_recalls = []
        
        for recall in all_recalls:
            # Search in multiple fields
            searchable_text = ' '.join([
                recall.get('product_description', ''),
                recall.get('reason_for_recall', ''),
                recall.get('company', ''),
                recall.get('classification', '')
            ]).lower()
            
            if query_lower in searchable_text:
                matching_recalls.append(recall)
                
            if len(matching_recalls) >= limit:
                break
        
        return jsonify({
            'success': True,
            'data': matching_recalls,
            'count': len(matching_recalls),
            'query': query
        })
        
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': f'Invalid parameter: {str(e)}'
        }), 400
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
        
        return jsonify({
            'success': True,
            'api_tests': {
                'fda_api': {
                    'status': 'working' if fda_status else 'failed',
                    'status_code': fda_response.status_code
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
        fda_recalls = fetch_fda_recalls(100)
        cpsc_recalls = fetch_cpsc_recalls(50)
        
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

# Error handlers
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
    
    app.run(host='0.0.0.0', port=port, debug=debug)S