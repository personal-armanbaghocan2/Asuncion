"""Own-account profile metadata and normalized private image storage."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import re

from flask import Response, abort, flash, redirect, request, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException, ServiceUnavailable
import server_sessions_v0_1_0 as sessions

MAX_UPLOAD=2*1024*1024
MAX_PHOTO=262144
DEFAULT_AVATAR=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" rx="40" fill="#e7f1eb"/><circle cx="40" cy="29" r="15" fill="#176147"/><path d="M12 76v-8a28 28 0 0 1 56 0v8" fill="#176147"/></svg>'


def image_bytes(upload):
    if not upload or not upload.filename:abort(400,description='Choose a profile photo.')
    content=upload.stream.read(MAX_UPLOAD+1)
    if not content or len(content)>MAX_UPLOAD:abort(413,description='Use a profile image below 2 MB.')
    try:
        with Image.open(BytesIO(content)) as probe:
            if probe.format not in ('JPEG','PNG','WEBP') or getattr(probe,'n_frames',1)!=1:
                raise ValueError()
            if probe.width*probe.height>4_000_000 or min(probe.size)<16:raise ValueError()
            probe.verify()
        with Image.open(BytesIO(content)) as original:
            image=ImageOps.exif_transpose(original).convert('RGB')
            image=ImageOps.fit(image,(256,256),method=Image.Resampling.LANCZOS)
            image.info.clear()
            stream=BytesIO();image.save(stream,format='JPEG',quality=85,optimize=True)
        data=stream.getvalue()
        if len(data)>MAX_PHOTO:raise ValueError()
        return data
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):
        abort(400,description='Use a valid, single-frame JPEG, PNG or WebP profile photo.')


def expected_revision():
    text=request.form.get('revision','')
    if not re.fullmatch(r'[0-9]{1,18}',text):abort(400,description='Reopen My Account before editing.')
    return int(text)


@contextmanager
def connection(api,write=False):
    uid=session.get('user_id');digest=sessions.token_hash(session.get(sessions.COOKIE_KEY))
    if type(uid) is not int or uid<=0 or not digest:abort(403)
    con=None
    try:
        con=api['conn']()
        if not write:con.execute('SET TRANSACTION READ ONLY')
        con.execute("SET LOCAL statement_timeout = '5s'")
        con.execute("SET LOCAL lock_timeout = '5s'")
        if write:
            sessions._ready(con)
            # User then session lock order matches password/session updates;
            # do not upgrade a preceding shared user lock.
            actor=con.execute('select id,role,status from users where id=? for update',(uid,)).fetchone()
            token=con.execute('select user_id from auth_sessions where user_id=? and token_hash=? and revoked_at is null and expires_at>clock_timestamp() for share',(uid,digest)).fetchone()
            if not actor or actor['status']!='Active' or actor['role'] not in ('Admin','Staff','Inspector') or not token:abort(403)
        con.execute("select set_config('psms.workbook_actor',?,true)",(json.dumps(dict(uid=uid,sid=digest)),))
        yield con,uid
        if write:con.commit()
        else:con.rollback()
    except HTTPException:
        if con is not None:con.rollback()
        raise
    except Exception:
        if con is not None:
            try:con.rollback()
            except Exception:pass
        raise ServiceUnavailable('Private profile storage is unavailable. The reviewed account migration and grants must be installed.') from None
    finally:
        if con is not None:
            try:con.close()
            except Exception:
                raise ServiceUnavailable('Profile connection cleanup could not be confirmed. Reopen My Account before retrying.') from None


def state(api):
    with connection(api) as (con,uid):
        row=con.execute('select revision,updated_at from psms_accounts.profiles where user_id=?',(uid,)).fetchone()
        return dict(row) if row else dict(revision=0,updated_at=None)


def save(api):
    action=request.form.get('action','profile')
    allowed={'csrf_token','revision','action','full_name'} if action=='profile' else {'csrf_token','revision','action'}
    if set(request.form)-allowed or set(request.files)-{'photo'}:abort(400,description='Only your full name, profile picture and password may be changed.')
    if action not in ('profile','photo','remove_photo'):abort(400)
    if (action!='photo' and request.files) or (action=='photo' and len(request.files.getlist('photo'))!=1):
        abort(400,description='Submit one profile picture at a time.')
    name=request.form.get('full_name','').strip()
    if action=='profile' and (len(name)>160 or any(ord(c)<32 for c in name)):
        abort(400,description='Use a full name of at most 160 characters.')
    expected=expected_revision()
    data=image_bytes(request.files.get('photo')) if action=='photo' else None
    with connection(api,write=True) as (con,uid):
        current=con.execute('select revision from psms_accounts.profiles where user_id=? for update',(uid,)).fetchone()
        actual=current['revision'] if current else 0
        if actual!=expected:abort(409,description='Your profile changed in another session. Reopen My Account before saving.')
        if not current:
            con.execute('insert into psms_accounts.profiles(user_id) values(?)',(uid,))
        if action=='profile':
            con.execute('update users set full_name=?,display_name=? where id=?',(name,name[:80],uid))
            con.execute('update psms_accounts.profiles set revision=revision+1,updated_at=clock_timestamp() where user_id=?',(uid,))
        else:
            con.execute('update psms_accounts.profiles set photo_content=?,photo_mime=?,photo_sha256=?,revision=revision+1,updated_at=clock_timestamp() where user_id=?',
                (data,'image/jpeg' if data else None,sha256(data).hexdigest() if data else None,uid))
        api['log']('Updated Own Profile','Account',str(uid),'Updated '+action+'; account authority unchanged',connection=con)
    flash('Profile updated successfully.','success')
    return redirect(url_for('account'))


def photo(api):
    try:
        with connection(api) as (con,uid):
            row=con.execute('select photo_content,photo_mime,photo_sha256 from psms_accounts.profiles where user_id=?',(uid,)).fetchone()
            data=bytes(row['photo_content']) if row and row['photo_content'] else DEFAULT_AVATAR
            mime=row['photo_mime'] if row and row['photo_content'] else 'image/svg+xml'
    except ServiceUnavailable:
        # The existing application remains usable before the additive migration.
        data,mime=DEFAULT_AVATAR,'image/svg+xml'
    response=Response(data,mimetype=mime)
    response.set_etag(sha256(data).hexdigest())
    response.headers.update({'Cache-Control':'private, no-cache','X-Content-Type-Options':'nosniff',
                             'Content-Security-Policy':"default-src 'none'",'Vary':'Cookie'})
    return response.make_conditional(request)
