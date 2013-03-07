import base64

from functools import wraps

from django.contrib import auth
from django.shortcuts import get_object_or_404
from django.http import HttpResponse, HttpResponseBadRequest
from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.csrf import csrf_exempt

# astrometry.net imports
from astrometry.net.models import *
from astrometry.net.views.submission import handle_upload
from api_util import *
from log import *
from tmpfile import *
import settings

json_type = 'text/plain' # 'application/json'


class HttpResponseErrorJson(HttpResponse):
    def __init__(self, errstring):
        args = { 'status': 'error',
                 'errormessage': errstring }
        doc = python2json(args)
        super(HttpResponseErrorJson, self).__init__(doc, content_type=json_type)

class HttpResponseJson(HttpResponse):
    def __init__(self, args):
        doc = python2json(args)
        super(HttpResponseJson, self).__init__(doc, content_type=json_type)

def create_session(key):
    from django.conf import settings
    engine = __import__(settings.SESSION_ENGINE, {}, {}, [''])
    sess = engine.SessionStore(key)
    # sess.session_key creates a new key if necessary.
    return sess

# decorator for extracting JSON arguments from a POST.
def requires_json_args(handler):
    @wraps(handler)
    def handle_request(request, *pargs, **kwargs):
        #loginfo('POST: ' + str(request.POST))
        json = request.POST.get('request-json')
        loginfo('POST: JSON: "%s"' % json)
        if not json:
            loginfo('POST: keys: ', request.POST.keys())
            loginfo('GET keys: ', request.GET.keys())
            return HttpResponseErrorJson('no json')
        args = json2python(json)
        if args is None:
            return HttpResponseErrorJson('failed to parse JSON: "%s"' % json)
        request.json = args
        #print 'json:', request.json
        return handler(request, *pargs, **kwargs)
    return handle_request

# decorator for retrieving the user's session based on a session key in JSON.
# requires "request.json" to exist: you probably need to precede this decorator
# by the "requires_json_args" decorator.
def requires_json_session(handler):
    @wraps(handler)
    def handle_request(request, *args, **kwargs):
        #print 'requires_json_session decorator running.'
        if not 'session' in request.json:
            return HttpResponseErrorJson('no "session" in JSON.')
        key = request.json['session']
        session = create_session(key)
        if not session.exists(key):
            return HttpResponseErrorJson('no session with key "%s"' % key)
        request.session = session
        #print 'session:', request.session
        resp = handler(request, *args, **kwargs)
        session.save()
        # remove the session from the request so that SessionMiddleware
        # doesn't try to set cookies.
        del request.session
        return resp
    return handle_request

def requires_json_login(handler):
    @wraps(handler)
    def handle_request(request, *args, **kwargs):
        #print 'requires_json_login decorator running.'
        user = auth.get_user(request)
        #print 'user:', request.session
        if not user.is_authenticated():
            return HttpResponseErrorJson('user is not authenticated.')
        return handler(request, *args, **kwargs)
    return handle_request



def upload_common(request, url=None, file=None):
    df, original_filename = handle_upload(file=file, url=url)
    submittor = request.user if request.user.is_authenticated() else None

    json = request.json
    allow_commercial_use = json.get('allow_commercial_use', 'd')
    allow_modifications = json.get('allow_modifications', 'd')
    license,created = License.objects.get_or_create(
        default_license=submittor.get_profile().default_license,
        allow_commercial_use=allow_commercial_use,
        allow_modifications=allow_modifications,
    )
    publicly_visible = json.get('publicly_visible', 'y')

    subargs = dict(
        user=submittor,
        disk_file=df,
        original_filename=original_filename,
        license=license,
        publicly_visible=publicly_visible,
        )
    if url is not None:
        subargs.update(url=url)

    for key,typ in [('scale_units', str),
                    ('scale_type', str),
                    ('scale_lower', float),
                    ('scale_upper', float),
                    ('scale_est', float),
                    ('scale_err', float),
                    ('center_ra', float),
                    ('center_dec', float),
                    ('radius', float),
                    ('tweak_order', int),
                    ('downsample_factor', int),
                    ('use_sextractor', bool),
                    ('crpix_center', bool),
                    ('parity', int),
                    ]:
        if key in json:
            subargs[key] = typ(json[key])

    sub = Submission(**subargs)
    sub.save()
    return HttpResponseJson({'status': 'success',
                             'subid': sub.id,
                             'hash': sub.disk_file.file_hash}) 



@csrf_exempt
@requires_json_args
@requires_json_session
def url_upload(req):
    logmsg('request:' + str(req))
    url = req.json.get('url')
    logmsg('url: %s' % url)
    return upload_common(req, url=url)

@csrf_exempt
@requires_json_args
@requires_json_session
def api_upload(request):
    #logmsg('request:' + str(request))
    upfile = request.FILES.get('file', None)
    logmsg('api_upload: got file', upfile)
    logmsg('request.POST has keys:', request.POST.keys())
    logmsg('request.GET has keys:', request.GET.keys())
    logmsg('request.FILES has keys:', request.FILES.keys())
    #logmsg('api_upload: got request: ' + str(request.FILES['file'].size))
    return upload_common(request, file=request.FILES['file'])

def write_wcs_file(req, wcsfn):
    from astrometry.util import util as anutil
    wcsparams = []
    wcs = req.json['wcs']
    for name in ['crval1', 'crval2', 'crpix1', 'crpix2',
                 'cd11', 'cd12', 'cd21', 'cd22', 'imagew', 'imageh']:
        wcsparams.append(wcs[name])
    wcs = anutil.Tan(*wcsparams)
    wcs.write_to(wcsfn)


@csrf_exempt
@requires_json_args
@requires_json_session
def api_sdss_image_for_wcs(req):
    from sdss_image import plot_sdss_image
    wcsfn = get_temp_file()
    plotfn = get_temp_file()
    write_wcs_file(req, wcsfn)
    plot_sdss_image(wcsfn, plotfn)
    return HttpResponseJson({'status': 'success',
                             'plot': base64.b64encode(open(plotfn).read()),
                             })

@csrf_exempt
@requires_json_args
@requires_json_session
def api_galex_image_for_wcs(req):
    from galex_jpegs import plot_into_wcs
    wcsfn = get_temp_file()
    plotfn = get_temp_file()
    write_wcs_file(req, wcsfn)
    plot_into_wcs(wcsfn, plotfn, basedir=settings.GALEX_JPEG_DIR)
    return HttpResponseJson({'status': 'success',
                             'plot': base64.b64encode(open(plotfn).read()),
                             })

@csrf_exempt
@requires_json_args
@requires_json_session
def api_submission_images(req):
    logmsg('request:' + str(req))
    subid = req.json.get('subid')
    try:
        sub = Submission.objects.get(pk=subid)
    except Submission.DoesNotExist:
        return HttpResponseErrorJson("submission does not exist")
    image_ids = []
    for image in sub.user_images.all():
        image_ids += [image.id]
    return HttpResponseJson({'status': 'success',
                             'image_ids': image_ids})

@csrf_exempt
@requires_json_args
def api_login(request):
    apikey = request.json.get('apikey')
    if apikey is None:
        return HttpResponseErrorJson('need "apikey"')

    loginfo('Got API key:', apikey)
    try:
        profile = UserProfile.objects.all().get(apikey=apikey)
        loginfo('Matched API key: ' + str(profile))
    except ObjectDoesNotExist:
        loginfo('Bad API key')
        return HttpResponseErrorJson('bad apikey')
    except:
        import traceback
        loginfo('Error matching API key: ' + traceback.format_exc())
        raise
        

    # Successful API login.  Register on the django side.

    # can't do this:
    #password = profile.user.password
    #auth.authenticate(username=profile.user.username, password=password)
    #auth.login(request, profile.user)

    loginfo('backends:' + str(auth.get_backends()))

    user = profile.user
    user.backend = 'django_openid_auth.auth.OpenIDBackend'

    session = create_session(None)

    request.session = session
    auth.login(request, profile.user)
    # so that django middleware doesnt' try to set cookies in response
    del request.session
    session.save()

    key = session.session_key
    loginfo('Returning session key ' + key)
    return HttpResponseJson({ 'status': 'success',
                              'message': 'authenticated user: ' + str(profile.user.email),
                              'session': key,
                              })

@csrf_exempt
def submission_status(req, sub_id):
    sub = get_object_or_404(Submission, pk=sub_id)
    jobs = []
    for job in sub.get_best_jobs():
        if job is None:
            jobs.append(None)
        else:
            jobs.append(job.id)
    json_response = {
        'user':sub.user.id,
        'processing_started':str(sub.processing_started),
        'processing_finished':str(sub.processing_finished),
        'user_images':[image.id for image in sub.user_images.all()],
        'jobs':jobs,
    }

    if sub.error_message:
        json_response.update({'error_message':sub.error_message})
    return HttpResponseJson(json_response)

@csrf_exempt
def job_status(req, job_id):
    job = get_object_or_404(Job, pk=job_id)
    status = job.get_status_blurb()
    return HttpResponseJson({
        'status':status,
    })

@csrf_exempt
def calibration(req, job_id):
    job = get_object_or_404(Job, pk=job_id)
    if job.calibration:
        cal = job.calibration
        (ra, dec, radius) = cal.get_center_radecradius()
        pixscale = cal.raw_tan.get_pixscale()
        orient = cal.raw_tan.get_orientation()
        return HttpResponseJson({
            'ra':ra,
            'dec':dec,
            'radius':radius,
            'pixscale':pixscale,
            'orientation':orient,
        })
    else:
        return HttpResponseJson({
            'error':'no calibration data available for job %d' % int(job_id)
        })

@csrf_exempt
def tags(req, job_id):
    job = get_object_or_404(Job, pk=job_id)
    tags = job.user_image.tags.all()
    json_tags = [tag.text for tag in tags]
    return HttpResponseJson({
        'tags':json_tags}
    )

@csrf_exempt
def machine_tags(req, job_id):
    job = get_object_or_404(Job, pk=job_id)
    machine_user = User.objects.get(username=MACHINE_USERNAME)
    tags = TaggedUserImage.objects.filter(
        user_image = job.user_image,
        tagger = machine_user
    )
    json_tags = [tagged_user_image.tag.text for tagged_user_image in tags]
    return HttpResponseJson({
        'tags':json_tags}
    )

@csrf_exempt
def objects_in_field(req, job_id):
    job = get_object_or_404(Job, pk=job_id)
    sky_objects = job.user_image.sky_objects.all()
    json_sky_objects = [sky_obj.name for sky_obj in sky_objects]
    return HttpResponseJson({
        'objects_in_field':json_sky_objects}
    )

@csrf_exempt
def jobs_by_tag(req):
    query = req.GET.get('query')
    exact = req.GET.get('exact')
    images = UserImage.objects.all()
    job_ids = []
    if exact:
        try:
            tag = Tag.objects.filter(text__iexact=query).get()
            images = images.filter(tags=tag)
            job_ids = [[job.id for job in image.jobs.all()] for image in images]
        except Tag.DoesNotExist:
            images = UserImage.objects.none() 
    else:
        images = images.filter(tags__text__icontains=query)
        job_ids = [[job.id for job in image.jobs.all()] for image in images]

    # flatten job_ids list
    if job_ids:
        job_ids = [id for sublist in job_ids for id in sublist]

    return HttpResponseJson({
        'job_ids':job_ids}
    )
