from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


WATCHING = "WATCHING"
STABLE_ON = "STABLE_ON"
STABLE_OFF = "STABLE_OFF"
PENDING_OFF = "PENDING_OFF"
ALERT_OFF = "ALERT_OFF"
RECOVERED = "RECOVERED"


@dataclass
class SubscriberSnapshot:
    subscriber_key: str
    parent_port_key: str
    olt_name: str
    batch_id: str
    status: str
    measured_at: datetime
    ma_tb: str = ""
    ten_tb: str = ""
    ma_men: str = ""
    doi_vt: str = ""
    diachi_ld: str = ""
    dienthoai_lh: str = ""
    ten_nvkt_db: str = ""
    account_fiber: str = ""
    onu_last_off: str = ""
    onu_last_on: str = ""

    @property
    def port_id(self) -> str:
        return self.subscriber_key


@dataclass
class OutageAlert:
    subscriber_key: str
    parent_port_key: str
    batch_id: str
    ma_tb: str = ""
    ten_tb: str = ""
    ma_men: str = ""
    olt_name: str = ""
    doi_vt: str = ""
    diachi_ld: str = ""
    dienthoai_lh: str = ""
    ten_nvkt_db: str = ""
    first_on_time: Optional[datetime] = None
    first_off_time: Optional[datetime] = None
    alert_time: Optional[datetime] = None
    off_duration_minutes: int = 0
    consecutive_on_count: int = 0
    notification_sent: bool = False
    notification_time: Optional[datetime] = None
    suppressed_by_pattern: bool = False
    suppressed_by_wide_area: bool = False
    suppression_reason: str = ""
    id: Optional[int] = None

    @property
    def port_id(self) -> str:
        return self.subscriber_key


@dataclass
class RecoveryAlert:
    subscriber_key: str
    parent_port_key: str
    batch_id: str
    ma_tb: str = ""
    ten_tb: str = ""
    olt_name: str = ""
    doi_vt: str = ""
    diachi_ld: str = ""
    dienthoai_lh: str = ""
    ten_nvkt_db: str = ""
    outage_time: Optional[datetime] = None
    recovery_time: Optional[datetime] = None
    outage_duration_minutes: int = 0
    notification_sent: bool = False
    notification_time: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def port_id(self) -> str:
        return self.subscriber_key


@dataclass
class WideAreaAlert:
    batch_id: str
    parent_port_key: str
    olt_name: str
    port: str
    subscriber_count: int
    incident_type: str = "wide_area"
    subscriber_keys: List[str] = field(default_factory=list)
    subscriber_list: List[dict] = field(default_factory=list)
    doi_vt: str = ""
    alert_time: Optional[datetime] = None
    first_off_time: Optional[datetime] = None
    off_duration_minutes: int = 0
    notification_sent: bool = False
    notification_time: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def olt_port_key(self) -> str:
        return self.parent_port_key
