# JSON Report Indexing & Retrieval System

It is a**semantic search and retrieval system** for JSON-based analytical reports. It  indexes structured data (datasets, charts, metadata) into a SQLite database with full-text search capabilities, then provides a natural language query interface to retrieve relevant information.

**Core Value Proposition:** Transform complex JSON reports into a queryable knowledge base that understands user intent and returns contextually relevant data.

---

## 🏗️ Architecture

The system consists of two main layers:

```
┌─────────────────────────────────────────────────────────┐
│                    USER QUERY                           │
│              "Show me bar charts for August 2024"       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────────────────┐
│              RETRIEVAL LAYER                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Intent     │  │   Search     │  │   Packet     │  │
│  │Classification│  │   Engine     │  │  Assembly    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└──────────────────────┬─────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              INDEXING LAYER                             │
│  ┌──────────────────────────────────────────────────┐   │
│  │        SQLite Database (FTS5-enabled)            │   │
│  │  • Chunks table (structured data)                │   │
│  │  • FTS index (full-text search)                  │   │
│  │  • Triggers (auto-sync)                          │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              SOURCE DATA                                │
│         JSON Reports (credit_report.json)               │
│  • Datasets with time-series data                       │
│  • Chart/visualization configurations                   │
│  • Metadata and schema information                      │
└─────────────────────────────────────────────────────────┘
```

---

## 📦 Component Breakdown

### **1. Indexing Layer** (`index_json/`)

#### **indexing.py** - Core Indexing Engine

**Purpose:** Parses JSON reports and stores them as searchable "chunks" in SQLite.

**Key Concepts:**

- **Chunking Strategy:** Breaks JSON into semantic units:
  
  - `dataset/header` - Dataset metadata (row counts, date ranges)
  - `dataset/schema/column` - Column definitions
  - `dataset/row` - Individual data rows (if dataset < 100K rows)
  - `viz/chart` - Chart specifications (type, fields, styling)
  - `project/meta` - Document-level metadata

- **Database Schema:**
  
  ```sql
  chunks (
      doc_id,          -- Document identifier
      pointer,         -- JSON Pointer (RFC 6901): /dataFiles/0/data/5
      jsonpath,        -- JSONPath notation: $.dataFiles[0].data[5]
      domain,          -- Semantic category: "dataset/row", "viz/chart"
      subtype,         -- Subcategory: "bar", "line" (for charts)
      rendered_text,   -- Human-readable summary
      json_blob,       -- Compact structured data
      links_json,      -- Relationships (dataset_id, fields)
      date_str,        -- ISO date for temporal queries
      date_num,        -- Unix timestamp for range queries
      ...
  )
  ```

- **FTS5 Integration:**
  
  - External-content FTS table (`chunks_fts`)
  - Auto-synced via triggers (INSERT/UPDATE/DELETE)
  - Enables fast full-text search across all chunks

**Key Functions:**

- `ensure_db()` - Creates schema with FTS5 tables and triggers
- `index_report_to_sqlite()` - Main indexing pipeline
- `chart_to_semantic_spec()` - Extracts chart semantics (dimensions, measures, styling)

**Example Chunk:**

```json
{
  "doc_id": "credit_report.json",
  "pointer": "/infoElements/rootTags/0",
  "domain": "viz/chart",
  "subtype": "bar",
  "rendered_text": "bar: Charge over Date | stacked",
  "json_blob": {
    "chart_type": "bar",
    "dataset_id": "Sheet_1",
    "dimension_fields": ["Date"],
    "measure_fields": ["Charge"],
    "stacking": true
  }
}
```

---

#### **json_to_csv.py** - Data Export Utility

**Purpose:** Converts JSON report data to CSV format for external analysis.

**Process:**

1. Extracts `dataFiles[0]` from JSON
2. Maps column indices to human-readable headers
3. Writes to CSV with proper encoding

---

#### **jsonpointer.py** - Path Utilities

**Purpose:** Generates RFC 6901 JSON Pointers and JSONPath expressions.

**Why It Matters:** Enables precise chunk location tracking for later retrieval.

**Example:**

```python
segments = ["dataFiles", 0, "data", 5]
to_pointer(segments)   # → "/dataFiles/0/data/5"
to_jsonpath(segments)  # → "$.dataFiles[0].data[5]"
```

---

### **2. Retrieval Layer** (`retrieval_layer/`)

#### **intent.py** - Query Understanding

**Purpose:** Classifies user queries and extracts structured information.

**Intent Categories:**

1. **`viz`** - Visualization queries
   
   - Keywords: "chart", "graph", "plot", "bar", "area", "pie"
   - Example: "Show me the bar chart for August"

2. **`agg`** - Aggregation queries
   
   - Keywords: "average", "sum", "total", "max", "trend"
   - Example: "What's the total charge in 2024?"

3. **`meta`** - Metadata queries
   
   - Keywords: "fields", "columns", "schema", "records"
   - Example: "How many rows are in the dataset?"

4. **`data`** - Direct data retrieval
   
   - Keywords: "show me", "get", "find", "values"
   - Example: "Get data for August 15, 2024"

**Date Parsing:** Supports multiple formats:

```
✓ ISO: 2024-08-15
✓ Natural: "August 15, 2024", "7th Aug 2024"
✓ Relative: "last month", "this year"
✓ Ranges: "from Aug 2024 to Sep 2024"
✓ Month/Year: "August 2024", "2024"
```

**Key Functions:**

- `route_intent()` - Classifies query intent with confidence scoring
- `parse_dates()` - Extracts date ranges from natural language
- `extract_field_names()` - Identifies mentioned column names
- `analyze_query()` - Complete query analysis in one call

---

#### **search.py** - Database Query Engine

**Purpose:** Provides specialized search functions for different data types.

**Search Strategies:**

1. **Full-Text Search (FTS):**
   
   ```python
   fts_search("bar chart opacity", limit=10, domain_filter="viz/chart")
   ```
   
   - Uses FTS5 MATCH queries with ranking
   - Supports operators: AND, OR, NOT, wildcards (*)
   - Auto-joins with chunks table for full payloads

2. **Date-Based Retrieval:**
   
   ```python
   search_rows_by_date_range(doc_id, dataset_id, "2024-08-01", "2024-08-31")
   search_rows_by_exact_date(doc_id, dataset_id, "2024-08-15")
   search_rows_by_month(doc_id, dataset_id, "2024-08")
   search_rows_by_year(doc_id, dataset_id, 2024)
   ```
   
   - Uses indexed `date_num` (Unix timestamp) for efficient range queries
   - Uses `date_str`, `date_month`, `date_year` for exact matches

3. **Structured Lookups:**
   
   ```python
   find_dataset_header(doc_id, "Sheet_1")
   find_column_schemas(doc_id, "Sheet_1", ["Date", "Charge"])
   get_charts_for_dataset(doc_id, "Sheet_1")
   ```

**Performance Optimizations:**

- Indexed columns: `domain`, `doc_id`, `date_num`, `subtype`
- External-content FTS (no duplication)
- Efficient JSON field searching with LIKE patterns

---

#### **packet.py** - Context Assembly

**Purpose:** Assembles retrieved chunks into a structured "context packet" for LLM consumption.

**Packet Structure:**

```
======================================================================
[CONTEXT BEGIN]
======================================================================
Document ID: credit_report.json
Total Chunks: 15
======================================================================

### DATASET/HEADER (1 items)
----------------------------------------------------------------------
[1] DATASET/HEADER
    Path: /dataFiles/0
    Description: Dataset Sheet_1 | rows=1247 | date[2024-01-01..2024-12-31]
    Data: {"dataset_id":"Sheet_1","records_found":1247,...}

### VIZ/CHART (3 items)
----------------------------------------------------------------------
[2] VIZ/CHART/BAR
    Path: /infoElements/rootTags/0
    Description: bar: Charge over Date | stacked
    Data: {"chart_type":"bar","dimension_fields":["Date"],...}
...
======================================================================
[CONTEXT END]
======================================================================
```

**Features:**

- **Smart Enrichment:** Automatically adds related schemas and headers
- **Grouping:** Organizes by domain for clarity
- **Truncation:** Respects token budgets (configurable max)
- **Metadata Control:** Optional inclusion of paths and technical details

**Key Functions:**

- `assemble_packet()` - Main assembly orchestrator
- `estimate_token_count()` - Approximates LLM token usage (~4 chars/token)
- `get_packet_summary()` - Extracts statistics for debugging

---

#### **planner.py** - Query Orchestration

**Purpose:** Routes queries to optimal retrieval strategies based on intent.

**Decision Flow:**

```
User Query → Analyze Intent & Extract Dates/Fields
              ↓
         Route by Intent:
              ↓
    ┌─────────┴─────────┬────────────┬────────────┐
    ↓                   ↓            ↓            ↓
  VIZ                 META         AGG          DATA
    ↓                   ↓            ↓            ↓
Get charts        Get schemas  Get rows for  Get specific
matching query    and stats    aggregation   data rows
    ↓                   ↓            ↓            ↓
         Assemble Context Packet
                   ↓
            Return to User/LLM
```

**Intent-Specific Handlers:**

1. **`_handle_viz_query()`**
   
   - Strategy 1: If dataset known → get its charts
   - Strategy 2: FTS search for chart type + fields
   - Strategy 3: Return any charts as fallback
   - **Scoring:** Ranks charts by field relevance and query matches

2. **`_handle_meta_query()`**
   
   - Returns dataset headers with optional schemas
   - Handles "how many records" efficiently without fetching rows

3. **`_handle_agg_query()`**
   
   - If dates provided → fetch rows in range (up to 500 for aggregations)
   - Optimizes query type (exact date vs month vs year vs range)
   - If no dates → provide structure for user guidance

4. **`_handle_data_query()`**
   
   - Date-based retrieval (up to 50 rows for display)
   - FTS fallback with field boosting
   - Helpful error messages with suggestions

**Auto-Detection:**

- `_detect_doc_id()` - Uses first document if not specified
- `_detect_dataset_id()` - Infers from available datasets

---

#### **config.py** - Configuration

**Purpose:** Centralizes settings and limits.

**Key Settings:**

- `MAX_PACKET_ITEMS = 20` - Max chunks per context
- `MAX_JSON_BLOB_INLINE = 1200` - Size threshold for inlining JSON
- `MAX_ROWS_DATA = 50` - Rows for data queries
- `MAX_ROWS_AGG = 500` - Rows for aggregations

---

#### **store.py** - Database Utilities

**Purpose:** Low-level database connection and FTS detection.

**Key Functions:**

- `connect()` - Creates connection with row factory
- `fts_is_external_content()` - Detects FTS configuration
- `row_to_dict()` - Converts rows to dicts, parsing JSON fields

---

## 🔄 Complete Workflow Example

### **Scenario:** User asks: *"Show me bar charts with high opacity for August 2024"*

**Step 1: Intent Analysis** (`intent.py`)

```python
analysis = analyze_query(query)
# Result:
{
  "intent": "viz",           # Visualization query
  "dates": ("2024-08-01", "2024-08-31"),  # Extracted date range
  "fields": [],
  "confidence": "high"
}
```

**Step 2: Query Planning** (`planner.py`)

```python
# Routes to _handle_viz_query()
# Builds FTS query: "bar opacity"
# Filters by domain: "viz/chart"
```

**Step 3: Search Execution** (`search.py`)

```python
hits = fts_search("bar opacity", domain_filter="viz/chart")
# Returns 5 chart chunks with matching specs
```

**Step 4: Context Assembly** (`packet.py`)

```python
packet = assemble_packet(doc_id, hits, add_schema=True, add_header=True)
# Enriches with:
# - Dataset header (for context)
# - Column schemas for "Date" and "Charge" fields
# - Organized by domain
```

**Step 5: Return Formatted Context**

```
======================================================================
[CONTEXT BEGIN]
======================================================================
Document ID: credit_report.json
Total Chunks: 8
======================================================================

### DATASET/HEADER (1 items)
----------------------------------------------------------------------
[1] DATASET/HEADER
    Description: Dataset Sheet_1 | rows=1247 | date[2024-01-01..2024-12-31]
    ...

### DATASET/SCHEMA/COLUMN (2 items)
----------------------------------------------------------------------
[2] DATASET/SCHEMA/COLUMN
    Description: Column Date | type=date | min=2024-01-01 max=2024-12-31
    ...

### VIZ/CHART (5 items)
----------------------------------------------------------------------
[3] VIZ/CHART/BAR
    Description: bar: Charge over Date | opacity=0.8
    Data: {"chart_type":"bar","opacity":0.8,...}
...
```

---

## 🎯 Key Design Patterns

### **1. Semantic Chunking**

- Each chunk = one logical unit (row, chart, schema)
- Enables precise retrieval without over-fetching
- Rich metadata for intelligent filtering

### **2. Multi-Strategy Retrieval**

- FTS for text-based queries
- Indexed date fields for temporal queries
- Structured lookups for known relationships

### **3. Intent-Driven Architecture**

- Query classification → optimal strategy selection
- Reduces irrelevant results
- Improves retrieval precision

### **4. Progressive Enhancement**

- Base chunks + optional enrichment (schemas, headers)
- Respects token budgets
- Configurable verbosity

### **5. Date Intelligence**

- Multiple date formats supported
- Relative date parsing ("last month")
- Efficient range queries via indexed timestamps

---

## 🚀 Usage Examples

### **Indexing a Report**

```python
from index_json.indexing import index_report_to_sqlite

index_report_to_sqlite(
    json_path="reports/credit_report.json",
    db_path="database/index_json.db",
    doc_id="credit_report.json"
)
```

### **Querying**

```python
from retrieval_layer.planner import handle_query

# Natural language query
context = handle_query(
    question="What's the average charge in August 2024?",
    db_path="database/index_json.db"
)

# Returns formatted context packet ready for LLM
print(context)
```

### **Direct Search**

```python
from retrieval_layer.search import fts_search, search_rows_by_month

# Full-text search
charts = fts_search("stacked bar", domain_filter="viz/chart")

# Date-based retrieval
rows = search_rows_by_month("credit_report.json", "Sheet_1", "2024-08")
```

---

# API Server Layer - Additional Documentation

This document contains the additional sections added to explain the FastAPI server (`app.py`).

---

## 🌐 API Server Layer (`app.py`)

### **FastAPI REST API**

**Purpose:** Production-ready HTTP server that exposes the retrieval system and LLM integration as a web service.

**Architecture:**

```
HTTP Request → FastAPI → Retrieval Layer → Database
                  ↓
            LLM (Groq API)
                  ↓
           JSON Response
```

---

### **Core Endpoints**

#### **1. Main Query Endpoint: `/agent/dispatch`**

**Purpose:** Primary interface for question answering with full RAG (Retrieval-Augmented Generation) pipeline.

**Request:**

```json
POST /agent/dispatch
{
  "query": "What is the average charge in August 2024?",
  "doc_id": "credit_report.json",         // Optional, auto-detected
  "dataset_id": "Sheet_1",                // Optional, auto-detected
  "intent": "agg",                        // Optional, auto-classified
  "include_context": false,               // Include raw context in response
  "context": {                            // Optional page context from frontend
    "title": "Credit Card Report",
    "url": "https://example.com/report",
    "selection": "highlighted text...",
    "nearest_heading": {"text": "Monthly Summary"},
    "tables": [{"id": "table1", "rows": 50, "cols": 5}]
  }
}
```

**Response:**

```json
{
  "answer": "The average charge in August 2024 was $1,234.56...",
  "context": "...",  // Only if include_context=true
  "citations": [],   // Future: source citations
  "metadata": {
    "intent": "agg",
    "detected_intent": "agg",
    "confidence": "high",
    "context_tokens": 250,
    "response_time_ms": 850,
    "dates_detected": ["2024-08-01", "2024-08-31"],
    "fields_detected": ["Charge"]
  }
}
```

**Processing Flow:**

1. **Query Validation** - Ensure non-empty query
2. **Intent Analysis** - Classify query intent (`intent.py`)
3. **Context Retrieval** - Fetch relevant chunks (`planner.py`)
4. **Message Building** - Construct LLM prompt with system instructions
5. **LLM Generation** - Call Groq API with retry logic
6. **Response Assembly** - Package answer with metadata

---

#### **2. Debug Endpoints**

**Statistics:** `GET /debug/stats`

```json
{
  "system": {
    "model": "llama-3.1-70b-versatile",
    "debug_mode": false,
    "timestamp": "2024-01-08T10:30:00Z"
  },
  "database": {
    "total_chunks": 15432,
    "chunks_by_domain": {
      "dataset/row": 10000,
      "viz/chart": 50,
      "dataset/header": 5
    },
    "date_range": {"min": "2024-01-01", "max": "2024-12-31"}
  },
  "documents": ["credit_report.json", "sales_report.json"]
}
```

**List Documents:** `GET /debug/docs`

```json
{
  "documents": ["credit_report.json", "sales_report.json"],
  "count": 2
}
```

**List Charts:** `GET /debug/charts?doc_id=credit_report.json`

```json
{
  "charts": [
    {
      "doc_id": "credit_report.json",
      "pointer": "/infoElements/rootTags/0",
      "subtype": "bar",
      "rendered_text": "bar: Charge over Date | stacked",
      "json_blob": {"chart_type": "bar", ...}
    }
  ],
  "count": 5
}
```

**List Datasets:** `GET /debug/datasets?doc_id=credit_report.json`

```json
{
  "datasets": [
    {
      "doc_id": "credit_report.json",
      "pointer": "/dataFiles/0",
      "rendered_text": "Dataset Sheet_1 | rows=1247 | date[2024-01-01..2024-12-31]"
    }
  ],
  "count": 1
}
```

**Full-Text Search:** `GET /debug/fts?q=bar+chart&limit=10&doc_id=credit_report.json`

```json
{
  "query": "bar chart",
  "results": [...],
  "count": 5
}
```

**Query Analysis:** `POST /debug/analyze?query=Show me August 2024 data`

```json
{
  "query": "Show me August 2024 data",
  "intent": "data",
  "dates": ["2024-08-01", "2024-08-31"],
  "fields": [],
  "confidence": "high",
  "intent_scores": {"viz": 0, "agg": 0, "meta": 0, "data": 3}
}
```

**Health Check:** `GET /health`

```json
{
  "status": "healthy",
  "timestamp": "2024-01-08T10:30:00Z",
  "model": "llama-3.1-70b-versatile",
  "version": "1.0.0"
}
```

---

### **LLM Integration**

#### **System Prompt Management**

**Purpose:** Loads system instructions from `context_LLM.txt` that guide the LLM's behavior.

**Template Structure:**

```
You are an AI assistant that helps users understand analytical reports.

CONTEXT:
{context}

INSTRUCTIONS:
- Provide accurate, concise answers based on the context
- Cite specific data points when possible
- If information is not in context, say so clearly
- Format numbers appropriately (e.g., $1,234.56)
...
```

**Dynamic Context Injection:**

- Retrieved chunks are formatted and injected into `{context}` placeholder
- Page context (title, URL, selection) added when available
- Intent classification passed to help LLM understand query type

---

#### **Groq API Client**

**Features:**

1. **Retry Logic with Exponential Backoff**
   
   ```python
   max_retries = 4
   backoff_delays = [0, 2, 4, 8]  # seconds
   ```
   
   - Handles rate limits (429) and service unavailable (503)
   - Exponential backoff prevents API overwhelm

2. **Timeout Handling**
   
   ```python
   async with httpx.AsyncClient(timeout=30.0) as client:
      response = await client.post(...)
   ```
   
   - 30-second timeout for LLM responses
   - Graceful timeout handling with retries

3. **Debug Logging**
   
   ```
   When DEBUG_LLM=1:
   - Logs system/user message lengths
   - Shows preview of prompts (first 800 chars)
   - Logs response preview (first 200 chars)
   - Tracks token counts and response times
   ```

4. **Error Handling**
   
   - Distinguishes between retriable (429, 503) and fatal errors
   - Provides clear error messages to clients
   - Logs full error context for debugging

---

### **Configuration Management**

**Environment Variables (`.env`):**

```bash
# Required
GROQ_API_KEY=gsk_xxxxxxxxxxxxx

# Optional
MODEL=llama-3.1-70b-versatile    # LLM model to use
MAX_TOKENS=700                    # Max response length
TEMPERATURE=0.1                   # Sampling temperature (0-1)
DEBUG_LLM=0                       # Enable verbose logging (0/1)
CORS_ORIGINS=*                    # Allowed CORS origins (comma-separated)
JSON_INDEX_DB=database/index_json.db  # Database path
```

**Default Values:**

- Model: `llama-3.1-70b-versatile` (Groq's fast 70B model)
- Max Tokens: `700` (concise answers)
- Temperature: `0.1` (low randomness for factual answers)
- CORS: `*` (allow all origins - restrict in production!)

---

### **Request/Response Models (Pydantic)**

**Benefits:**

- **Type Safety:** Automatic validation of request/response data
- **Documentation:** Auto-generated OpenAPI/Swagger docs at `/docs`
- **Error Handling:** Clear validation errors (400 Bad Request)

**Models:**

```python
class ContextInfo(BaseModel):
    """Page context from frontend (browser extension, etc.)"""
    title: Optional[str]
    url: Optional[str]
    selection: Optional[str]           # User-highlighted text
    nearest_heading: Optional[Dict]    # Contextual heading
    anchors: Optional[List[Dict]]      # Links on page
    tables: Optional[List[Dict]]       # Tables detected

class QueryRequest(BaseModel):
    """Main query request"""
    query: str                         # Required
    intent: Optional[str]              # Optional, auto-detected
    context: Optional[ContextInfo]     # Optional page context
    doc_id: Optional[str]              # Optional, auto-detected
    dataset_id: Optional[str]          # Optional, auto-detected
    include_context: bool = False      # Include raw retrieval context

class QueryResponse(BaseModel):
    """Query response with metadata"""
    answer: str                        # LLM-generated answer
    context: Optional[str]             # Raw context (if requested)
    citations: List[str]               # Source citations (TODO)
    metadata: Dict[str, Any]           # Query metadata
```

---

### **Middleware & Extensions**

#### **CORS Middleware**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # Configure for production!
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True
)
```

**Purpose:** Enables browser-based clients (web UIs, extensions) to call the API.

#### **Static Files**

```python
app.mount("/reports_html", StaticFiles(directory="reports_html"))
```

**Purpose:** Serves HTML reports at `http://localhost:8000/reports_html/`

---

### **Production Deployment**

**Running the Server:**

```bash
# Development
uvicorn app:app --reload --port 8000

# Production (with Gunicorn + Uvicorn workers)
gunicorn app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
```

---

### **Complete Request Flow Example**

**Scenario:** User asks *"What was my highest charge in August 2024?"*

**1. Client Request:**

```bash
curl -X POST http://localhost:8000/agent/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What was my highest charge in August 2024?",
    "include_context": false
  }'
```

**2. Server Processing:**

```
[Server] Request received: query="What was my highest charge in August 2024?"
[Intent] Analyzing query...
[Intent] Detected: intent=agg, dates=(2024-08-01, 2024-08-31), confidence=high
[Planner] Routing to: _handle_agg_query
[Search] Executing: search_rows_by_month("credit_report.json", "Sheet_1", "2024-08")
[Search] Retrieved: 247 rows
[Packet] Assembling context: 247 rows + 1 header + 2 schemas = 250 chunks
[Packet] Token estimate: ~2,100 tokens
[LLM] Building messages: system=2,500 chars, user=150 chars
[LLM] Calling Groq API: model=llama-3.1-70b-versatile, temp=0.1, max_tokens=700
[LLM] Response received: 350 tokens, time=1.2s
[Server] Success: total_time=1.85s
```

**3. Server Response:**

```json
{
  "answer": "Based on the data for August 2024, your highest charge was $2,847.32 on August 15th. This was followed by $1,956.00 on August 22nd and $1,423.89 on August 8th.",
  "context": null,
  "citations": [],
  "metadata": {
    "intent": "agg",
    "detected_intent": "agg",
    "confidence": "high",
    "context_tokens": 2100,
    "response_time_ms": 1850,
    "dates_detected": ["2024-08-01", "2024-08-31"],
    "fields_detected": ["Charge"]
  }
}
```

---

### **Key Design Decisions**

1. **Async Architecture**
   
   - FastAPI with async/await for concurrent requests
   - Non-blocking LLM calls via httpx.AsyncClient
   - Scales to hundreds of concurrent users

2. **Separation of Concerns**
   
   - Server layer: HTTP handling, validation, LLM integration
   - Retrieval layer: Query understanding, context assembly
   - Indexing layer: Data storage, search

3. **Defensive Error Handling**
   
   - Retry logic for transient failures
   - Clear error messages for debugging
   - Graceful degradation (e.g., partial results)

4. **Observability**
   
   - Structured logging at every layer
   - Debug endpoints for system introspection
   - Response metadata for analytics

5. **Flexibility**
   
   - Optional parameters (doc_id, dataset_id auto-detect)
   - Configurable via environment variables
   - Support for page context from frontends

---

## 🔗 Complete System Integration

### **End-to-End Data Flow**

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. DATA PREPARATION                                              │
│    JSON Report → indexing.py → SQLite Database                   │
│    (One-time indexing or periodic updates)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 2. USER INTERACTION                                              │
│    User → Web UI/API Client → POST /agent/dispatch              │
│    "Show me bar charts for August 2024"                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 3. INTENT ANALYSIS (intent.py)                                   │
│    • Classify: "viz" (visualization query)                       │
│    • Extract dates: (2024-08-01, 2024-08-31)                    │
│    • Confidence: "high"                                          │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 4. QUERY PLANNING (planner.py)                                   │
│    • Route to: _handle_viz_query()                               │
│    • Strategy: FTS search for "bar chart" + date filter         │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 5. DATABASE RETRIEVAL (search.py)                                │
│    • FTS search: domain="viz/chart", query="bar"                │
│    • Filter by linked dataset dates                             │
│    • Retrieve: 5 matching charts                                │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 6. CONTEXT ASSEMBLY (packet.py)                                  │
│    • Primary: 5 chart specs                                      │
│    • Enrichment: dataset header + column schemas                │
│    • Format: Structured text packet (~2,000 tokens)             │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 7. LLM GENERATION (app.py)                                       │
│    • Build prompt: system message + context + user query        │
│    • Call Groq API: llama-3.1-70b-versatile                     │
│    • Response: "Here are the bar charts for August 2024..."     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│ 8. RESPONSE DELIVERY                                             │
│    Server → JSON Response → User                                 │
│    {answer, metadata, citations}                                │
└─────────────────────────────────────────────────────────────────┘
```

---

### **Technology Stack Summary**

| Layer           | Technology               | Purpose                                     |
| --------------- | ------------------------ | ------------------------------------------- |
| **API Server**  | FastAPI + Uvicorn        | HTTP endpoints, async handling              |
| **LLM**         | Groq API (Llama 3.1 70B) | Natural language generation                 |
| **Retrieval**   | Custom Python modules    | Intent classification, search orchestration |
| **Database**    | SQLite + FTS5            | Indexed storage, full-text search           |
| **Indexing**    | Python + JSON parsing    | Data extraction, chunking                   |
| **HTTP Client** | httpx                    | Async LLM API calls                         |
| **Validation**  | Pydantic                 | Request/response models                     |
| **Logging**     | Python logging           | Observability                               |

---

### **Deployment Architecture Options**

**Option 1: Monolithic (Simple)**

```
┌─────────────────────────────┐
│   Single Container/VM        │
│  ┌─────────────────────┐    │
│  │   FastAPI Server    │    │
│  │   (app.py)          │    │
│  └──────────┬──────────┘    │
│             │                │
│  ┌──────────▼──────────┐    │
│  │  SQLite Database    │    │
│  └─────────────────────┘    │
└─────────────────────────────┘
```

**Option 2: Decoupled (Scalable)**

```
┌──────────────┐     ┌──────────────┐
│   API Server │────▶│  Groq Cloud  │
│   (FastAPI)  │     └──────────────┘
└──────┬───────┘
       │
       │ Network
       │
┌──────▼───────┐
│   Database   │
│   (SQLite    │
│    or Postgres)│
└──────────────┘
```

**Option 3: Cloud-Native (Production)**

```
┌────────────────────────────────────────┐
│         Load Balancer (ALB/NGINX)      │
└────────────┬───────────────────────────┘
             │
    ┌────────┴────────┐
    │                 │
┌───▼────┐      ┌────▼───┐
│ Server │      │ Server │  (Multiple replicas)
│ Pod 1  │      │ Pod 2  │
└───┬────┘      └────┬───┘
    │                │
    └────────┬───────┘
             │
    ┌────────▼────────┐
    │ Managed DB      │
    │ (RDS/CloudSQL)  │
    └─────────────────┘
```

---
