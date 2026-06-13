from dataclasses import dataclass, field


@dataclass
class Lead:
    """Represents a single business lead extracted from any source."""

    name: str
    email: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""
    category: str = ""
    rating: float = 0.0
    source: str = ""
    raw_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return all fields as a flat dictionary."""
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "website": self.website,
            "address": self.address,
            "category": self.category,
            "rating": self.rating,
            "source": self.source,
            "raw_data": self.raw_data,
        }
