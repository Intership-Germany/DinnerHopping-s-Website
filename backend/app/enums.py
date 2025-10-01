from enum import Enum


class Gender(str, Enum):
    female = 'female'
    male = 'male'
    diverse = 'diverse'
    prefer_not_to_say = 'prefer_not_to_say'

    @classmethod
    def normalize(cls, value):
        if value is None:
            return cls.prefer_not_to_say
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:  # noqa: BLE001
            allowed = ', '.join(member.value for member in cls)
            raise ValueError(f"gender must be one of: {allowed}") from exc


class DietaryPreference(str, Enum):
    vegan = 'vegan'
    vegetarian = 'vegetarian'
    omnivore = 'omnivore'

    @classmethod
    def normalize(cls, value):
        if value is None or value == '':
            return None
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:  # noqa: BLE001
            allowed = ', '.join(member.value for member in cls)
            raise ValueError(f"dietary_preference must be one of: {allowed}") from exc


class CoursePreference(str, Enum):
    appetizer = 'appetizer'
    main = 'main'
    dessert = 'dessert'

    @classmethod
    def normalize(cls, value):
        if value is None or value == '':
            return None
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:  # noqa: BLE001
            allowed = ', '.join(member.value for member in cls)
            raise ValueError(f"course_preference must be one of: {allowed}") from exc
