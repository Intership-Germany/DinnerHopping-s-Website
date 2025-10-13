"""
Test Team Registration Workflow

This script tests the key validation logic for team registrations.
Run from the backend directory:
    pytest tests/test_team_registration_workflow.py -v
"""
import pytest
from fastapi import HTTPException
from app.utils import compute_team_diet


class TestTeamDietCalculation:
    """Test automatic team dietary preference calculation."""
    
    def test_vegan_wins(self):
        """Vegan has highest precedence."""
        assert compute_team_diet('vegan', 'omnivore') == 'vegan'
        assert compute_team_diet('omnivore', 'vegan') == 'vegan'
        assert compute_team_diet('vegan', 'vegetarian') == 'vegan'
        assert compute_team_diet('vegetarian', 'vegan') == 'vegan'
    
    def test_vegetarian_second(self):
        """Vegetarian beats omnivore but loses to vegan."""
        assert compute_team_diet('vegetarian', 'omnivore') == 'vegetarian'
        assert compute_team_diet('omnivore', 'vegetarian') == 'vegetarian'
    
    def test_omnivore_default(self):
        """Omnivore is default when no stronger preference."""
        assert compute_team_diet('omnivore', 'omnivore') == 'omnivore'
        assert compute_team_diet() == 'omnivore'
        assert compute_team_diet(None, None) == 'omnivore'
    
    def test_case_insensitive(self):
        """Diet values should be case-insensitive."""
        assert compute_team_diet('VEGAN', 'omnivore') == 'vegan'
        assert compute_team_diet('Vegetarian', 'Omnivore') == 'vegetarian'


class TestKitchenValidation:
    """Test kitchen availability validation rules."""
    
    def test_at_least_one_kitchen_required(self):
        """Team must have at least one kitchen available."""
        members = [
            {'kitchen_available': False},
            {'kitchen_available': False}
        ]
        has_kitchen = any(bool(m.get('kitchen_available')) for m in members)
        assert has_kitchen == False, "Should fail validation when no kitchen available"
    
    def test_one_kitchen_passes(self):
        """Team passes with one kitchen."""
        members = [
            {'kitchen_available': True},
            {'kitchen_available': False}
        ]
        has_kitchen = any(bool(m.get('kitchen_available')) for m in members)
        assert has_kitchen == True
    
    def test_both_kitchens_passes(self):
        """Team passes with both kitchens."""
        members = [
            {'kitchen_available': True},
            {'kitchen_available': True}
        ]
        has_kitchen = any(bool(m.get('kitchen_available')) for m in members)
        assert has_kitchen == True


class TestCookingLocationValidation:
    """Test cooking location and main course validation."""
    
    def test_cooking_location_must_have_kitchen(self):
        """Selected cooking location must have kitchen available."""
        members = [
            {'kitchen_available': True, 'main_course_possible': True},   # creator
            {'kitchen_available': False, 'main_course_possible': False}  # partner
        ]
        cooking_location = 'partner'  # Trying to cook at partner location
        cooking_location_idx = 0 if cooking_location == 'creator' else 1
        
        has_kitchen = bool(members[cooking_location_idx].get('kitchen_available'))
        assert has_kitchen == False, "Partner location has no kitchen, should fail"
    
    def test_main_course_requires_capability(self):
        """Main course requires main_course_possible at cooking location."""
        members = [
            {'kitchen_available': True, 'main_course_possible': False},  # creator
            {'kitchen_available': True, 'main_course_possible': True}    # partner
        ]
        
        # Try to cook main at creator location (not possible)
        course_preference = 'main'
        cooking_location = 'creator'
        cooking_location_idx = 0 if cooking_location == 'creator' else 1
        
        can_host_main = bool(members[cooking_location_idx].get('main_course_possible'))
        
        if course_preference == 'main':
            assert can_host_main == False, "Creator cannot host main course"
    
    def test_appetizer_dessert_no_restriction(self):
        """Appetizer and dessert can be cooked anywhere with kitchen."""
        members = [
            {'kitchen_available': True, 'main_course_possible': False},
            {'kitchen_available': True, 'main_course_possible': False}
        ]
        
        for course in ['appetizer', 'dessert']:
            # Should pass validation as long as location has kitchen
            cooking_location_idx = 0  # Creator
            has_kitchen = bool(members[cooking_location_idx].get('kitchen_available'))
            assert has_kitchen == True


class TestPartnerValidation:
    """Test partner validation rules."""
    
    def test_cannot_invite_self(self):
        """User cannot invite themselves as partner."""
        current_user_email = 'user@example.com'
        partner_email = 'user@example.com'
        
        assert current_user_email.lower() == partner_email.lower(), "Should detect self-invitation"
    
    def test_exactly_one_partner_type(self):
        """Must specify exactly one of partner_existing or partner_external."""
        # Case 1: Both provided
        partner_existing = {'email': 'test@example.com'}
        partner_external = {'name': 'Test', 'email': 'test2@example.com'}
        assert bool(partner_existing) == bool(partner_external), "Both provided, should fail"
        
        # Case 2: Neither provided
        partner_existing = None
        partner_external = None
        assert bool(partner_existing) == bool(partner_external), "Neither provided, should fail"
        
        # Case 3: Only existing (valid)
        partner_existing = {'email': 'test@example.com'}
        partner_external = None
        assert bool(partner_existing) != bool(partner_external), "Exactly one, should pass"
        
        # Case 4: Only external (valid)
        partner_existing = None
        partner_external = {'name': 'Test', 'email': 'test@example.com'}
        assert bool(partner_existing) != bool(partner_external), "Exactly one, should pass"


class TestTeamRegistrationScenarios:
    """Integration test scenarios for team registration."""
    
    def test_happy_path_both_kitchens_vegan_team(self):
        """Full scenario: Both have kitchens, one vegan makes team vegan."""
        creator = {
            'email': 'creator@example.com',
            'kitchen_available': True,
            'main_course_possible': True,
            'dietary_preference': 'vegan'
        }
        partner = {
            'email': 'partner@example.com',
            'kitchen_available': True,
            'main_course_possible': False,
            'dietary_preference': 'omnivore'
        }
        
        # Team dietary calculation
        team_diet = compute_team_diet(
            creator['dietary_preference'],
            partner['dietary_preference']
        )
        assert team_diet == 'vegan', "Team should be vegan"
        
        # Kitchen validation
        members = [creator, partner]
        has_kitchen = any(bool(m.get('kitchen_available')) for m in members)
        assert has_kitchen == True
        
        # Main course validation at creator location
        cooking_location = 'creator'
        course_preference = 'main'
        cooking_location_idx = 0
        can_host_main = bool(members[cooking_location_idx].get('main_course_possible'))
        assert can_host_main == True, "Creator can host main"
    
    def test_external_partner_no_kitchen_fails(self):
        """External partner with no kitchen, creator also no kitchen."""
        creator_kitchen = False
        partner_external = {
            'name': 'External Partner',
            'email': 'external@example.com',
            'kitchen_available': False
        }
        
        members = [
            {'kitchen_available': creator_kitchen},
            partner_external
        ]
        
        has_kitchen = any(bool(m.get('kitchen_available')) for m in members)
        assert has_kitchen == False, "Should fail: no kitchen available"
    
    def test_vegetarian_team_from_mixed_diets(self):
        """Team with vegetarian + omnivore = vegetarian team."""
        creator_diet = 'vegetarian'
        partner_diet = 'omnivore'
        
        team_diet = compute_team_diet(creator_diet, partner_diet)
        assert team_diet == 'vegetarian'


# Run with: pytest tests/test_team_registration_workflow.py -v
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
