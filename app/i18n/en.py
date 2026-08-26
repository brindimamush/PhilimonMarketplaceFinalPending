# app/i18n/en.py
STRINGS = {
    "language.choose": "Choose your language.",
    "language.updated": "Language updated.",
    "language.english": "English",
    "language.amharic": "Amharic",
    
    "buttons.register_buyer": "Register as Buyer",
    "buttons.register_seller": "Register as Seller",
    "buttons.support": "Support",
    "buttons.close": "Close",
    "buttons.edit": "Edit",
    "buttons.previous": "Previous",
    "buttons.next": "Next",
    "buttons.view_offers": "View Offers",
    "buttons.accept": "Accept",
    "buttons.reject": "Reject",
    "buttons.yes_reject": "Yes, Reject",
    "buttons.cancel": "Cancel",
    "buttons.submit": "Submit",
    "buttons.confirm": "Confirm",
    "buttons.i_agree": "I Agree",
    "buttons.share_my_phone": "Share My Phone",
    "buttons.choose_another": "Choose Another",
    "buttons.change_language": "Change Language",
    
    "start.welcome": "Welcome to the Marketplace Bot.\n\nChoose a registration path.",
    "start.dashboard": "Dashboard",
    "start.seller_approved": "Seller profile approved.",
    
    "suspended.message": "Your account has been suspended. Please use /support to contact support.",
    "operation.cancelled": "Operation cancelled. Use /start to try again or /support to get support.",
    "registration.buyer_complete": (
        "Buyer registration complete.\n\n"
        "Use /start to open the dashboard.\n\n"
        "To become a seller, press 'Register as Seller' on the dashboard."
    ),
    
    "seller.rules": "📜 Platform Rules: Seller Edition\n\nA. Only accept requests you can genuinely fulfill.\nB. Provide accurate pricing and product information.\nC. Repeated failure to respond may result in restrictions.",
    "seller.rules.agree": "I Agree",
    "seller.rules.decline": "I Decline",
    "seller.prompt.business_name": "Please enter your business name.",
    "seller.prompt.location": "Please enter your location.",
    "seller.prompt.category": "Please enter your product category.",
    "seller.prompt.shop_number": "🔢 Please enter your Shop Number.\n\nExample: X0-000X",
    "seller.confirm.title": "Confirm Seller Application",
    "seller.confirm.submit": "Confirm & Submit",
    "seller.confirm.edit": "Edit",
    "seller.confirm.cancel": "Cancel",
    "seller.application_submitted": "Your seller application has been submitted for admin approval.",
    "seller_app.approved": "✅ Your seller application has been approved.\n\nUse /start to open your dashboard.",
    "seller_app.declined": "❌ Your seller application was declined.\n\nIf you believe this is a mistake, use /support.",
    
    "rules.buyer": (
        "📜 Platform Rules: Buyer Edition\n\n"
        "A. Only submit requests if you genuinely intend to purchase.\n"
        "B. Upload only product-related images and information.\n"
        "C. Repeated abuse, spam, or non-serious requests may result in account restrictions."
    ),
    
    "prompt.share_ethiopian_phone": "Please share your Ethiopian Telegram phone number.",
    "prompt.full_name": "Please enter your full name.",
    "prompt.send_item_image": "Please send the item image.",
    "prompt.send_description": (
        "Please describe what the image represents.\n"
        "This helps sellers and admin clearly understand the request."
    ),
    "prompt.send_quantity": "Image received. Please enter quantity as a positive integer.",
    "prompt.support_description": "Please describe your problem in one message.",
    
    "error.invalid_full_name": "Please enter a valid full name.",
    "error.invalid_business_name": "Please enter a valid business name.",
    "error.invalid_location": "Please enter a valid location.",
    "error.invalid_category": "Please enter a valid product category.",
    "error.invalid_shop_number": "Please enter a valid shop number.",
    "error.invalid_description": "Description is too short. Please provide at least 5 characters.",
    "error.invalid_quantity": "Quantity must be a positive integer.",
    "error.invalid_price": "Invalid price. Example: 5000 or 5,000.50",
    "error.price_positive": "Price must be greater than zero.",
    "error.price_too_large": "Price is too large.",
    "error.ethiopian_phone": "Ethiopian phone number required.",
    "error.invalid_ethiopian_phone": "Invalid Ethiopian phone number.",
    "error.register_buyer_first": "Register as a buyer first.",
    "error.max_pending_requests": (
        "You can have at most {max} requests pending admin approval. "
        "Please wait until one is approved or declined before submitting another."
    ),
    "error.request_not_found": "Request not found.",
    "error.request_already_processed": "This request has already been processed.",
    "error.request_not_accepting_sellers": "This request is not accepting sellers.",
    "error.only_approved_sellers": "Only approved sellers can accept requests.",
    "error.cannot_accept_own_request": "You cannot accept your own request.",
    "error.already_submitted_offer": "You already submitted your offer for this request.",
    "error.already_rejected": "You already rejected this request.",
    "error.seller_capacity_full": "Seller capacity for this request is already full.",
    "error.request_not_accepting_offers": "This request is not accepting offers.",
    "error.not_accepted_seller": "You are not an accepted seller for this request.",
    "error.offer_not_found": "Offer not found.",
    "error.request_not_yours": "This request is not yours.",
    "error.offer_not_yours": "This offer is not yours.",
    "error.offer_not_available": "This offer is no longer available.",
    "error.offer_not_ready": "This request is not ready for offer selection.",
    "error.offer_no_longer_selectable": "This offer is no longer selectable.",
    "error.support_description_short": "Please describe your issue in at least 5 characters.",
    "error.seller_fields_required": "All seller registration fields are required.",
    "error.request_incomplete": "Request is incomplete.",
    "error.unknown_action": "Unknown action.",
    "error.unexpected_contact": "Unexpected contact. Use /start.",
    "error.unexpected_image": "Unexpected image. Use /start.",
    "error.use_start": "Use /start to continue.",
    "error.own_contact": "Please share your own Telegram phone number.",
    "error.unexpected_state": "Please follow the current prompt or use /start.",
    "error.generic": "Something went wrong while processing your request. Please try again. If the problem continues, use /support.",
    "error.rate_limited": "You are doing that too fast. Please wait a moment and try again.",
    
    "seller.label_full_name": "Full Name",
    "seller.label_phone": "Phone",
    "seller.label_business": "Business",
    "seller.label_location": "Location",
    "seller.label_category": "Category",
    "seller.label_shop_number": "Shop Number",
    
    "request.submitted": "Request {request_number} submitted and pending admin approval.",
    "request.declined_buyer": (
        "❌ Your request {request_number} was declined by admin.\n\n"
        "Reason:\n{reason}\n\n"
        "Please correct it and try again using /start."
    ),
    "request.label": "Request",
    "request.status": "Status",
    "request.qty_label": "Qty",
    "request.quantity": "Quantity",
    "request.description": "Description",
    "request.details_title": "Request Details",
    "request.summary_title": "Request Summary",
    
    "broadcast.new_request": (
        "New Purchase Request\n\n"
        "Request: {request_number}\n"
        "Quantity: {quantity}\n"
        "Description: {description}"
    ),
    
    "offer.submitted": "Offer submitted. The buyer will receive it now.",
    "offer.new_for_buyer": (
        "New offer for request {request_number}\n\n"
        "Price: {price} {currency}"
    ),
    "offer.list_caption": "Current offers for request {request_number}\n\nPlease choose an offer.",
    "offer.no_active": "No active offers available.",
    "offer.select_question_title": "Do you want to select this offer?",
    "offer.selected": "Offer selected. Admin will handle settlement next.",
    "offer.selected_seller": (
        "🎉 Your offer was selected.\n\n"
        "Request: {request_number}\n"
        "Your Price: {price} {currency}\n\n"
        "The administrator will contact you regarding the next settlement steps."
    ),
    "offer.details_title": "Offer Details",
    "offer.request_status": "Request Status",
    "offer.your_price": "Your Price",
    "offer.offer_status": "Offer Status",
    "offer.price_label": "Price",
    "offer.offer_label": "Offer {index}: {price} {currency}",
    
    "seller_request.accepted_enter_price": "Accepted. Please enter your price offer.",
    "seller_request.rejected": "You rejected this request.",
    "seller_request.reject_confirm": "Are you sure you want to reject this request?",
    "seller_request.rejection_cancelled": "Rejection cancelled",
    
    "my_requests.empty": "You have not sent any requests yet.",
    "my_requests.page": "My Requests — Page {page} / {total_pages}\nTotal: {total}",
    "my_offers.empty": "You have not submitted any offers yet.",
    "my_offers.page": "My Offers — Page {page} / {total_pages}\nTotal: {total}",
    
    "support.ticket_created": "Support ticket {ticket_number} created. Admin will review it.",
    "support.closed_user": (
        "Your support ticket {ticket_number} has been closed.\n\n"
        "Solution:\n{solution}"
    ),
    
    "settlement.message_closed": "Request {request_number} has been marked as settled.\n\nReason:\n{reason}",
    "settlement.message_pending": "Request {request_number} settlement is now pending.\n\nReason:\n{reason}",
    "settlement.message_cancelled": "Request {request_number} has been cancelled.\n\nReason:\n{reason}",
    
    "suspension.lifted": "Your account suspension has been lifted.",
    
    # Status Enum Translations
    "status.PENDING_ADMIN_APPROVAL": "Pending Admin Approval",
    "status.DECLINED": "Declined",
    "status.APPROVED": "Approved",
    "status.BROADCASTING": "Broadcasting",
    "status.COLLECTING_SELLERS": "Collecting Sellers",
    "status.COLLECTING_OFFERS": "Collecting Offers",
    "status.BUYER_SELECTING": "Buyer Selecting",
    "status.SELLER_SELECTED": "Seller Selected",
    "status.ADMIN_SETTLEMENT": "Admin Settlement",
    "status.CLOSED": "Closed",
    "status.CANCELLED": "Cancelled",
    "status.ACTIVE": "Active",
    "status.SELECTED": "Selected",
    "status.NOT_SELECTED": "Not Selected",
    "status.WITHDRAWN": "Withdrawn",
    "status.PENDING": "Pending",
    "status.OPEN": "Open",
    "status.IN_PROGRESS": "In Progress",
    "status.RESOLVED": "Resolved"
}