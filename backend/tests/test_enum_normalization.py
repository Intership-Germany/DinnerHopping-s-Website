from app.enums import CoursePreference, DietaryPreference, Gender, normalized_value


def test_normalized_value_accepts_enum_instance():
    assert normalized_value(CoursePreference, CoursePreference.main) == 'main'


def test_normalized_value_handles_case_insensitive_string():
    assert normalized_value(DietaryPreference, 'VEGAN') == 'vegan'


def test_normalized_value_returns_default_for_invalid_input():
    assert normalized_value(DietaryPreference, 'carnivore', default='omnivore') == 'omnivore'


def test_normalized_value_handles_none_or_blank():
    assert normalized_value(Gender, None) is None
    assert normalized_value(CoursePreference, '', default=None) is None
