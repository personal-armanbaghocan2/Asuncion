"""Shared, conservative notification semantics and non-sensitive error pages."""
import re
import secrets
from flask import current_app, get_flashed_messages, jsonify, render_template, request
from psycopg import Error as DatabaseError
from werkzeug.exceptions import SecurityError


WORKFLOW_UNAVAILABLE = {
    'PROCUREMENT_RELEASE_CONFIG_INVALID': 'The revised workflow requires its approved Vercel staging release configuration.',
    'PROCUREMENT_RELEASE_HOST_MISMATCH': 'Use the approved immutable staging URL. Localhost and the official website cannot use this release capability.',
    'PROCUREMENT_CAPABILITY_DENIED': 'This staging deployment is not authorized by the database, or its authorization expired or was revoked.',
    'PROCUREMENT_POLICY_PENDING': 'The procurement relationship authority or PO numbering policy has not been approved.',
    'PROCUREMENT_CONTRACT_UNAVAILABLE': 'The reviewed procurement SQL contract is missing or incompatible, or its required permissions are unavailable.',
    'PROCUREMENT_DATABASE_UNAVAILABLE': 'The procurement database connection or permissions could not be verified.',
}


def active_workspace():
    """Retain procurement navigation on errors using the matched endpoint only."""
    endpoint = (request.endpoint or '').split('.')[-1]
    workspace = endpoint.split('_', 1)[0]
    return workspace if workspace in ('aoq', 'po', 'nlp', 'iar') else ''


def database_failure(error):
    """Report an unconfirmed database operation without driver text or retries."""
    reference = secrets.token_hex(8)
    sqlstate = getattr(error, 'sqlstate', None)
    sqlstate = sqlstate if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate) else 'UNKNOWN'
    current_app.logger.warning('Database operation unavailable [DATABASE_OPERATION_FAILED] sqlstate=%s reference=%s',
                               sqlstate, reference)
    message = 'The database operation could not be confirmed. Reopen the latest record before retrying. Reference: ' + reference
    if request.is_json or request.accept_mimetypes.best == 'application/json':
        response = jsonify(saved=None, message=message, reference=reference, status=503)
        response.status_code = 503
    else:
        response = current_app.make_response((render_template('operation_error.html', active=active_workspace(),
            title='Operation needs attention', message=message, status=503), 503))
    response.headers['Cache-Control'] = 'private, no-store'
    return response


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
    app.register_error_handler(DatabaseError, database_failure)
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
        if isinstance(error, SecurityError):
            # A rejected Host has no URL adapter. Rendering a layout that calls
            # url_for() would replace the intended 400 with a second exception.
            response=app.response_class('The requested host is not authorized.',status=400,mimetype='text/plain')
            response.headers['Cache-Control']='private, no-store'
            return response
        message=public_messages.get(code,'The operation could not be completed. No success is assumed.')
        diagnostic=getattr(error,'psms_diagnostic',None)
        if code==503 and diagnostic in WORKFLOW_UNAVAILABLE:
            message=WORKFLOW_UNAVAILABLE[diagnostic]+' No legacy operation was attempted.'
            app.logger.warning('Procurement unavailable [%s]',diagnostic)
        if request.is_json or request.accept_mimetypes.best=='application/json':
            response=jsonify(saved=False,message=message,status=code);response.status_code=code
        else:
            response=app.make_response((render_template('operation_error.html',active=active_workspace(),title='Operation needs attention',
                message=message,status=code),code))
        response.headers['Cache-Control']='private, no-store'
        if code==503 and diagnostic in WORKFLOW_UNAVAILABLE:
            response.headers['X-PSMS-Diagnostic']=diagnostic
        return response
    for code in public_messages:app.register_error_handler(code,error_page)
