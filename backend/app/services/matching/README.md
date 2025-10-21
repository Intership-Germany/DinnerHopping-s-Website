# DinnerHopping Matching Algorithm Documentation

## Overview

The matching module implements a sophisticated algorithm to group participants for the DinnerHopping event.
Each event consists of three courses (appetizer, main course, dessert), and participants are organized into
groups of 3 teams (6 people total) for each course.

## Core Principles

### Team Formation
1. **Duos**: Teams of 2 people who register together
2. **Solos**: Individual participants who can be paired into teams
3. **Team Merging**: Solos are intelligently paired based on:
   - Geographic proximity
   - Dietary compatibility
   - Gender diversity (when possible)

### Matching Constraints

The algorithm ensures the following hard constraints:

1. **Each team hosts exactly once** - at their preferred course when possible
2. **Each team is a guest exactly twice** - at other teams' locations
3. **No team meets another team more than once** - ensures variety and new connections
4. **Host capabilities respected**:
   - Main course requires `can_host_main=true` (full kitchen)
   - Appetizer/dessert require `can_host_any=true` (basic kitchen)
5. **Dietary compatibility**:
   - Vegan hosts cannot accommodate omnivores
   - Vegetarian hosts cannot accommodate vegans
   - Omnivore hosts can accommodate all diets
6. **Allergy awareness**: Tracks and warns about uncovered allergies

### Optimization Goals

The algorithm optimizes for:

1. **Minimize total travel distance** - reduces travel time between locations
2. **Maximize course preference matches** - teams host their preferred course
3. **Minimize duplicate meetings** - penalizes teams meeting again
4. **Dietary compatibility** - ensures hosts can prepare suitable meals
5. **Allergy coverage** - hosts aware of all guest allergies

## Algorithm Implementations

### Greedy Algorithm (`algo_greedy`)

The default and most reliable algorithm:

**Process:**
1. Load all teams and build units (merging solos as needed)
2. Apply constraints (forced pairs, splits)
3. For each phase (appetizer → main → dessert):
   - Shuffle units slightly for variation
   - Select eligible hosts (respecting kitchen capabilities)
   - For each host candidate:
     - Evaluate all possible guest pairs
     - Score based on:
       - Course preference match
       - Distance/travel time
       - Dietary compatibility
       - Allergy coverage
       - Duplicate pair penalty
       - Transition distance from previous location
   - Select best scoring group
   - Mark teams as used and update locations

**Strengths:**
- Fast and reliable
- Good balance between quality and runtime
- Handles most event sizes efficiently

**Tuning:**
- `MATCH_HOST_CANDIDATES`: Limit hosts considered per round (default: 4)
- `MATCH_GUEST_CANDIDATES`: Limit guest pairs evaluated (default: 10)

### Random Algorithm (`algo_random`)

Similar to greedy but with more randomization:

**Process:**
- Same as greedy but shuffles units before each phase
- Introduces more variation in results
- Useful for comparing different matchings

### Local Search Algorithm (`algo_local_search`)

**Process:**
- Starts with greedy solution
- Attempts local improvements through team swaps
- Currently under development for enhancement

## Configuration

### Environment Variables

All matching parameters can be configured via environment variables:

#### Scoring Weights
```bash
MATCH_W_DUP=1000          # Penalty for duplicate team meetings
MATCH_W_DIST=0.5          # Weight for travel distance
MATCH_W_PREF=2            # Bonus for course preference match
MATCH_W_ALLERGY=2         # Penalty for allergy issues
MATCH_W_CAPABILITY=5      # Penalty for missing host capability
MATCH_W_TRANS=0           # Weight for transition distance
MATCH_W_FINAL_PARTY=0.3   # Weight for distance to after-party
MATCH_W_PHASE_ORDER=1     # Penalty for moving away from party
```

#### Algorithm Parameters
```bash
MATCH_HOST_CANDIDATES=4   # Number of hosts to evaluate per round
MATCH_GUEST_CANDIDATES=10 # Number of guest pairs to consider
MATCH_TRAVEL_FAST=false   # Use Haversine approximation vs. routing
MATCH_ROUTING_PARALLELISM=6  # Parallel routing requests
MATCH_GEOCODE_PARALLELISM=4  # Parallel geocoding requests
MATCH_ALLOW_TEAM_SPLITS=false  # Allow automatic team splitting
```

#### Course Configuration
```bash
MATCH_PHASES=appetizer,main,dessert  # Course sequence
MATCH_MEAL_TIME_APPETIZER=18:00      # Appetizer start time
MATCH_MEAL_TIME_MAIN=20:00           # Main course start time
MATCH_MEAL_TIME_DESSERT=22:00        # Dessert start time
```

## Data Flow

### Input (Registrations Collection)
```javascript
{
  event_id: ObjectId,
  user_email_snapshot: "user@example.com",
  team_id: ObjectId | null,  // null for solos
  team_size: 1 | 2,
  diet: "omnivore" | "vegetarian" | "vegan",
  allergies: ["peanuts", "shellfish"],
  preferences: {
    course_preference: "appetizer" | "main" | "dessert",
    kitchen_available: boolean,
    main_course_possible: boolean
  },
  status: "confirmed" | "paid" | ...
}
```

### Output (Matches Collection)
```javascript
{
  event_id: "string",
  version: 1,
  algorithm: "greedy",
  status: "proposed" | "finalized",
  groups: [
    {
      phase: "appetizer" | "main" | "dessert",
      host_team_id: "team_123",
      guest_team_ids: ["team_456", "team_789"],
      score: 12.5,
      travel_seconds: 900,
      warnings: ["diet_conflict", "allergy_uncovered"],
      host_address: "Full address",
      host_address_public: "Street, City",
      host_allergies: ["gluten"],
      guest_allergies: {
        "team_456": ["lactose"],
        "team_789": ["nuts"]
      },
      guest_allergies_union: ["lactose", "nuts"],
      uncovered_allergies: []
    }
  ],
  metrics: {
    total_travel_seconds: 5400,
    aggregate_group_score: 125.5,
    groups_with_warnings: 2
  },
  created_at: ISODate,
  finalized_at: ISODate
}
```

## Team Paths Output

The `compute_team_paths` function generates the itinerary for each team:

```javascript
{
  team_paths: {
    "team_123": {
      roles: {
        appetizer: { host: false, group_id: "grp_1" },
        main: { host: true, group_id: "grp_2" },
        dessert: { host: false, group_id: "grp_3" }
      },
      points: [
        { phase: "appetizer", lat: 48.8566, lon: 2.3522, is_host: false },
        { phase: "main", lat: 48.8606, lon: 2.3376, is_host: true },
        { phase: "dessert", lat: 48.8738, lon: 2.2950, is_host: false }
      ],
      distance_summary_km: 5.3,
      guest_lists: {
        main: ["team_456", "team_789"]  // guests when hosting
      },
      addresses: {
        appetizer: "Street Name, City",
        main: "Own Address",
        dessert: "Another Street, City"
      }
    }
  },
  bounds: {
    min_lat: 48.8566,
    max_lat: 48.8738,
    min_lon: 2.2950,
    max_lon: 2.3522
  }
}
```

## Usage

### Starting a Matching Job

```python
# Via API endpoint
POST /matching/{event_id}/start
{
  "algorithms": ["greedy", "random"],
  "weights": {
    "dist": 0.5,
    "pref": 2.0
  },
  "dry_run": false
}

# Response
{
  "status": "accepted",
  "job_id": "abc123...",
  "poll_url": "/matching/{event_id}/jobs/abc123..."
}
```

### Checking Job Status

```python
GET /matching/{event_id}/jobs/{job_id}

# Response
{
  "status": "completed",
  "progress": 1.0,
  "message": "Completed",
  "proposals": [
    {
      "algorithm": "greedy",
      "version": 1,
      "metrics": {
        "total_travel_seconds": 5400,
        "aggregate_group_score": 125.5,
        "groups_with_warnings": 2
      }
    }
  ]
}
```

### Retrieving Match Details

```python
GET /matching/{event_id}/details?version=1

# Response includes groups, metrics, and team_details with payment status
```

### Finalizing a Match

```python
POST /matching/{event_id}/finalize
{
  "version": 1
}

# This:
# 1. Marks the match as finalized
# 2. Generates individual team plans
# 3. Updates event matching_status to 'finalized'
# 4. Creates chat rooms for groups
```

## Validation and Issue Detection

The `list_issues` function identifies problems:

```python
GET /matching/{event_id}/issues?version=1

# Returns:
{
  "groups": [...],  # All groups from the match
  "issues": [
    {
      "type": "team_cancelled",
      "team_id": "team_123",
      "severity": "high"
    },
    {
      "type": "duplicate_pair",
      "teams": ["team_456", "team_789"],
      "count": 2,
      "severity": "high"
    },
    {
      "type": "payment_missing",
      "team_id": "team_abc",
      "severity": "medium"
    }
  ]
}
```

## Manual Adjustments

Admins can manually adjust matches:

### Move Team Between Groups
```python
POST /matching/{event_id}/move
{
  "version": 1,
  "phase": "main",
  "from_group_idx": 0,
  "to_group_idx": 1,
  "team_id": "team_123",
  "force": false
}
```

### Set Groups Manually
```python
POST /matching/{event_id}/set_groups
{
  "version": 1,
  "groups": [...],
  "force": false
}
```

### Validate Groups
```python
POST /matching/{event_id}/validate
{
  "groups": [...]
}

# Returns violations, phase_issues, group_issues
```

## Constraints System

### Forced Pairs
Force two solos to be paired together:

```python
POST /matching/{event_id}/constraints/pair
{
  "a_email": "alice@example.com",
  "b_email": "bob@example.com"
}
```

### Team Splits
Force a duo to be split into two solos:

```python
POST /matching/{event_id}/constraints/split
{
  "team_id": "team_123"
}
```

## Performance Considerations

### Fast Mode
- Enable `MATCH_TRAVEL_FAST=true` for large events (100+ teams)
- Uses Haversine distance approximation instead of routing API
- 10-100x faster but less accurate for urban areas with complex routing

### Candidate Limits
- Reduce `MATCH_HOST_CANDIDATES` and `MATCH_GUEST_CANDIDATES` for faster matching
- Increases speed but may reduce solution quality
- Recommended: 4-8 hosts, 10-20 guests for most events

### Parallelism
- Increase `MATCH_ROUTING_PARALLELISM` if routing service can handle it
- Increase `MATCH_GEOCODE_PARALLELISM` for faster initial geocoding
- Monitor API rate limits

## Troubleshooting

### Incomplete Matchings
If not all teams can be matched:
- Check team count is divisible by 3 (or use `MATCH_ALLOW_TEAM_SPLITS`)
- Verify kitchen capabilities are set correctly
- Review dietary distribution (too many vegans can limit options)
- Check valid_zip_codes filter on event

### Poor Distance Scores
- Ensure all users have valid addresses and are geocoded
- Run `backfill_user_geocodes.py` script
- Consider using fast mode for initial testing
- Review weight configuration

### Many Warnings
- `diet_conflict`: Host diet incompatible with guest
- `allergy_uncovered`: Host not aware of guest allergy
- `host_cannot_main`: Team hosting main without kitchen
- `host_no_kitchen`: Team hosting without any kitchen
- `host_reuse`: Team hosting multiple times (constraint violation)

These warnings indicate suboptimal matching but may be unavoidable with the given team distribution.

## Testing

### Unit Tests
```bash
cd backend
pytest tests/test_matching*.py -v
```

### Integration Test with Fake Data
```bash
cd backend
python scripts/fake_data_seeder.py --event-count 1 --teams 30
USE_FAKE_DB_FOR_TESTS=1 uvicorn app.main:app --reload
# Then use API to start matching
```

## Future Enhancements

1. **Advanced local search**: Implement 2-opt and 3-opt swaps
2. **Multi-objective optimization**: Pareto-optimal solutions
3. **Machine learning**: Learn from past successful matchings
4. **Real-time updates**: Live progress during matching
5. **Constraint relaxation**: Automatically relax constraints if no solution found
6. **Visualization**: Interactive map of team paths and groups

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [MongoDB Motor Driver](https://motor.readthedocs.io/)
- [OSRM Routing Engine](http://project-osrm.org/)
- [Haversine Formula](https://en.wikipedia.org/wiki/Haversine_formula)
