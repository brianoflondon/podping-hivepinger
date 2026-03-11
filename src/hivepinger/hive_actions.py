import logging
from decimal import Decimal
from typing import Any, Dict, List

from nectar.exceptions import MissingKeyError
from nectar.hive import Hive
from nectarapi.exceptions import RPCError, UnhandledRPCError


def get_hive_client(keys: List[str], nobroadcast: bool = False) -> Hive:
    """
    Initializes and returns a Hive client instance.

    Args:
        keys (List[str]): A list of private keys to initialize the Hive client.
        nobroadcast (bool, optional): If True, the Hive client will be initialized in no-broadcast mode.
            Defaults to False.

    Returns:
        Hive: An instance of the Hive client initialized with the provided keys and broadcast setting.
    """
    default_nodes = Hive().get_default_nodes()
    extra_nodes = [
        "https://rpc.podping.org",
    ]
    all_nodes = extra_nodes + default_nodes
    return Hive(node=all_nodes, keys=keys, nobroadcast=nobroadcast)


class CustomJsonSendError(Exception):
    """Custom exception for errors when sending custom JSON operations to Hive."""

    def __init__(self, message: str, extra: Dict[str, Any] | None = None):
        super().__init__(message)
        self.extra = extra or {}


async def send_custom_json(
    json_data: Dict[str, Any],
    send_account: str,
    hive_client: Hive | None = None,
    keys: List[str] = [],
    id: str = "pp_startup",
    nobroadcast: bool = False,
) -> Dict[str, str]:
    """
    Asynchronously sends a custom JSON operation to the Hive blockchain.

    This function allows sending a custom JSON operation with specified parameters
    to the Hive blockchain. It supports both active and posting authority, and can
    be configured to either broadcast the transaction or not.

    Args:
        json_data (dict): The JSON data to be sent. Must be a non-empty dictionary.
        send_account (str): The Hive account that will send the custom JSON operation.
        hive_client (Hive | None, optional): An instance of the Hive client. If not provided,
            a new client will be created using the provided keys. Defaults to None.
        keys (List[str], optional): A list of private keys to initialize the Hive client
            if `hive_client` is not provided. Defaults to an empty list.
        id (str, optional): The custom JSON operation ID. Defaults to "v4vapp_transfer".
        nobroadcast (bool, optional): If True, the transaction will not be broadcasted.
            Defaults to False.
        active (bool, optional): If True, the operation will require active authority.
            If False, it will require posting authority. Defaults to True.

    Returns:
        Dict[str, str]: The transaction response from the Hive blockchain.

    Raises:
        ValueError: If `json_data` is not a dictionary, is empty, or if neither `hive_client`
            nor `keys` are provided.
        CustomJsonSendError: If an error occurs while sending the custom JSON operation.
    """
    # Need Required_auths not posting auths for a transfer
    # test json data is a dict which will become a nice json object:

    json_data_converted: Dict[str, Any] = convert_decimals_to_float_or_int(json_data)

    if not isinstance(json_data_converted, dict):
        raise ValueError("json_data must be a dictionary")
    if not json_data_converted:
        raise ValueError("json_data must not be empty")
    if not hive_client and not keys:
        raise ValueError("No hive_client or keys provided")
    if not hive_client:
        hive_client = get_hive_client(keys=keys, nobroadcast=nobroadcast)

    try:
        kwargs = {"required_posting_auths": [send_account]}
        trx = hive_client.custom_json(
            id=id, json_data=json_data_converted, **kwargs, nobroadcast=nobroadcast
        )
        return trx
    except UnhandledRPCError as ex:
        logging.warning(
            f"Error sending custom_json: {ex}",
            extra={"notification": False, "send_account": send_account},
        )
        raise CustomJsonSendError(
            f"Error sending custom_json: {ex}",
            extra={
                "json_data": json_data_converted,
                "send_account": send_account,
                "nobroadcast": nobroadcast,
            },
        )
    except MissingKeyError as ex:
        logging.warning(
            f"Error sending custom_json: MissingKeyError: {ex}",
            extra={"send_account": send_account},
        )
        raise CustomJsonSendError("Wrong key used", extra={"send_account": send_account})
    except RPCError as ex:
        msg = str(ex)
        if "has not enough RC mana" in msg:
            # handle rate‑limit / RC‑exhaustion, back off, etc.
            logging.warning(
                f"Rate limit / RC exhaustion: {msg}", extra={"send_account": send_account}
            )
            raise CustomJsonSendError(
                "Rate limit / RC exhaustion", extra={"send_account": send_account}
            )
        else:
            # re‑raise or wrap as before
            raise CustomJsonSendError(f"RPC error: {msg}", extra={"send_account": send_account})
    except Exception as ex:
        logging.exception(ex, extra={"notification": False})
        logging.error(f"{send_account} {ex} {ex.__class__}")
        raise CustomJsonSendError(f"Error sending custom_json: {ex}")


def convert_decimals_to_float_or_int(
    obj: Any,
) -> Any:
    """
    Recursively converts Decimal and Decimal128 instances within a nested structure (dicts, lists) to appropriate Python types:
    - Whole-number Decimals to Python int.
    - Fractional Decimals to Python float.
    - Preserves other types and nested structures.

    Args:
        obj: The input object, which can be a dict, list, Decimal, Decimal128, or any other type.
    Returns:
        The input object with all Decimal and Decimal128 instances converted to int or float as appropriate.
        The structure of dicts and lists is preserved.

    Example:
        >>> from decimal import Decimal
        >>> from bson.decimal128 import Decimal128
        >>> convert_decimals_to_float_or_int({'a': Decimal('12345678901234567890'), 'b': Decimal('1.23'), 'c': [Decimal('2.0'), 3]})
        {'a': 12345678901234567890, 'b': 1.23, 'c': [2.0, 3]}
    """
    if isinstance(obj, dict):
        return {k: convert_decimals_to_float_or_int(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals_to_float_or_int(item) for item in obj]
    elif isinstance(obj, Decimal):
        # Check if the Decimal is a whole number (no fractional part)
        if obj == obj.to_integral_value():
            return int(obj)  # Convert to Python int for MongoDB int64
        else:
            return float(obj)  # Convert to Python float for MongoDB Decimal128
    else:
        return obj
