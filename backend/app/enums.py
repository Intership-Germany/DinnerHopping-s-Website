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


def normalized_value(enum_cls, value, default=None):
    """Return the normalized string value for an enum, falling back to default when invalid."""
    if value is None or value == '':
        return default
    # Prefer custom normalize implementation when available
    normalizer = getattr(enum_cls, 'normalize', None)
    if callable(normalizer):
        try:
            normalized = normalizer(value)
        except ValueError:
            return default
        if normalized is None:
            return default
        if isinstance(normalized, enum_cls):
            return normalized.value
        return normalized
    if isinstance(value, enum_cls):
        return value.value
    candidate = str(value).strip().lower()
    if not candidate:
        return default
    try:
        return enum_cls(candidate).value
    except ValueError:
        return default
