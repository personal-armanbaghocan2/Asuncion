"""Shared, conservative notification semantics and non-sensitive error pages."""
import re
from flask import get_flashed_messages, jsonify, render_template, request


def notification_category(category,message):
    first=str(message).split('. ',1)[0].split(';',1)[0].casefold()
    failed=bool(re.search(r'\b(wrong|incorrect|invalid|failed|failure|unable|cannot|denied|unavailable|rejected|expired)\b|could not|not found|do not match|does not match|not confirmed|^no (?:items?|records?|changes?) (?:were )?(?:saved|created|updated|applied)',first))
    if failed:return 'error'
    known={'danger':'error','error':'error','success':'success','warning':'info','info':'info'}
    if category in known:return known[category]
    if re.search(r'\b(already|pending|not|no)\b',first):return 'info'
    if re.search(r'\b(successfully|saved|updated|uploaded|created|generated|approved|imported|refreshed|completed)\b',first):
        return 'success'
    return 'info'


def register(app):
    def messages(with_categories=False,category_filter=()):
        pairs=get_flashed_messages(with_categories=True,category_filter=category_filter)
        values=[(notification_category(category,message),message) for category,message in pairs]
        return values if with_categories else [message for _,message in values]
    app.jinja_env.globals['get_flashed_messages']=messages
    public_messages={400:'The submitted values are invalid. Review the form and try again.',
        403:'You do not have permission to perform this operation.',404:'The requested record is unavailable.',
        409:'This record changed or conflicts with another operation. Reopen its latest review before saving.',
        413:'The upload exceeds the supported size. Reduce or split the file before retrying.',
        422:'The submitted document or values need correction. Review the original source.',
        503:'This operation is temporarily unavailable. Your previous records remain retained; reopen the review before retrying.'}
    def error_page(error):
        code=error.code
        message=public_messages.get(code,'The operation could not be completed. No success is assumed.')
        if request.is_json or request.accept_mimetypes.best=='application/json':
            response=jsonify(saved=False,message=message,status=code);response.status_code=code
        else:
            response=app.make_response((render_template('operation_error.html',active='',title='Operation needs attention',
                message=message,status=code),code))
        response.headers['Cache-Control']='private, no-store'
        return response
    for code in public_messages:app.register_error_handler(code,error_page)
