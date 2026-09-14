from bot.models.cart import CartItem
from bot.models.product import Product


def test_cart_summary_items_are_rendered_as_cart_items():
    item = CartItem(quantity=2)
    item.product = Product(title="Test product")

    assert item.title == "Test product"
    assert item.quantity == 2
