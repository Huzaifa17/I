from flask import Flask, render_template, request, redirect, url_for, session, flash
import re  # Add this import at the top of the file
from utils import make_links_clickable  # Import the function
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
import os
import bleach
from werkzeug.utils import secure_filename
from datetime import datetime  # Add this import at the top of the file

# Connect to MongoDB
client = MongoClient(os.getenv('MONGO_URI'))
db = client.flask_db  # Replace 'flask_db' with your database name

# Update notifications to add a default 'type' field
result = db.notifications.update_many(
    { 'type': { '$exists': False } },
    { '$set': { 'type': 'general' } }
)


# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# MongoDB setup
client = MongoClient(os.getenv('MONGO_URI'))
db = client.flask_db
users = db.users
posts = db.posts
notifications = db.notifications
comments = db.comments

# Ensure admin and moderator roles exist
if users.count_documents({'username': 'admin'}) == 0:
    users.insert_one({'username': 'admin', 'email': 'admin@example.com', 'password': 'admin123', 'role': 'admin'})

# Configure upload folder
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Helper functions
def is_admin():
    return 'username' in session and users.find_one({'username': session['username'], 'role': 'admin'})

def is_moderator():
    return 'username' in session and users.find_one({'username': session['username'], 'role': 'moderator'})


# Helper function to add notifications
# def add_notification(message, link=None):
#     notification = {
#         'message': message,
#         'link': link,  # Store the link to the user profile or post
#         'timestamp': datetime.now()
#     }
#     notifications.insert_one(notification)

# Make helper functions available in templates
@app.context_processor
def utility_processor():
    return dict(is_admin=is_admin, is_moderator=is_moderator)


# Helper function to convert URLs into clickable links
def make_links_clickable(text):
    # Regex to detect URLs
    url_pattern = re.compile(r'https?://\S+')
    # Replace URLs with clickable links
    return url_pattern.sub(r'<a href="\g<0>" target="_blank">\g<0></a>', text)

# Make the helper function available in templates
@app.context_processor
def utility_processor():
    return dict(is_admin=is_admin, is_moderator=is_moderator, make_links_clickable=make_links_clickable)


def is_strong_password(password):
    """
    Check if the password meets the following criteria:
    - At least 8 characters long.
    - Contains at least one uppercase letter.
    - Contains at least one lowercase letter.
    - Contains at least one digit.
    - Contains at least one special character.
    """
    if len(password) < 8:
        return False
    if not re.search(r'[A-Z]', password):  # Uppercase letter
        return False
    if not re.search(r'[a-z]', password):  # Lowercase letter
        return False
    if not re.search(r'[0-9]', password):  # Digit
        return False
    if not re.search(r'[!@#$%^&*()]', password):  # Special character
        return False
    return True


def make_links_clickable(text):
    """
    Convert URLs in text to clickable links (colored blue and opening in a new tab).
    """
    url_pattern = re.compile(r'https?://\S+')
    return url_pattern.sub(r'<a href="\g<0>" target="_blank" style="color: blue;">\g<0></a>', text)



# 1. Updated Notification Model
def add_notification(notification_type, message, target_user=None, target_post=None):
    """Add a notification with contextual linking"""
    # Sanitize the message to prevent XSS
    sanitized_message = bleach.clean(message, tags=['a'], attributes={'a': ['href']})
    
    notification = {
        'type': notification_type,
        'message': sanitized_message,
        'timestamp': datetime.now()
    }
    if target_user:
        notification['target_user_id'] = target_user['_id']
        notification['link'] = url_for('profile', username=target_user['username'])
    if target_post:
        notification['target_post_id'] = target_post['_id']
        notification['link'] = url_for('view_topic', post_id=str(target_post['_id']))
    
    notifications.insert_one(notification)



# 2. Context Processor for Notification Links
@app.context_processor
def inject_notification_utils():
    def notification_link(notification):
        if notification['type'] == 'moderator':
            user = users.find_one({'_id': notification['target_user_id']})
            return url_for('profile', username=user['username']) if user else '#'
        elif notification['type'] in ['post_approved', 'post_updated', 'post_deleted']:
            post = posts.find_one({'_id': notification['target_post_id']})
            if notification['type'] == 'post_updated':
                return url_for('edit_post', post_id=post['_id']) if post else '#'
            elif notification['type'] == 'post_deleted':
                return url_for('home')  # Redirect to home for deleted posts
            return url_for('view_topic', post_id=post['_id']) if post else '#'
        return '#'
    return {'notification_link': notification_link}


@app.template_filter('datetimeformat')
def datetimeformat(value, format='%Y-%m-%d %H:%M'):
    return value.strftime(format)



# Routes
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = users.find_one({'email': email, 'password': password})
        if user:
            session['username'] = user['username']
            return redirect(url_for('home'))
        else:
            flash('Invalid email or password', 'error')
    return render_template('login.html')


@app.route('/edit_post/<post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    # Ensure only the post owner can edit the post
    if post['username'] != session['username']:
        flash('You do not have permission to edit this post.', 'error')
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        attachments = request.files.getlist('attachments')
        
        # Save new attachments if provided
        attachment_urls = post.get('attachment_urls', [])
        for attachment in attachments:
            if attachment and allowed_file(attachment.filename):
                filename = secure_filename(attachment.filename)
                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                attachment.save(attachment_path)
                attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
        
        # Update the post
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$set': {
                    'title': title,
                    'content': content,
                    'attachment_urls': attachment_urls
                }
            }
        )
        
        # Add notification for post update
        add_notification(
            'post_updated',
            f"📝 {session['username']} updated the post: {title}",
            target_post=post
        )
        
        flash('Post updated successfully!', 'success')
        return redirect(url_for('profile', username=session['username']))
    
    return render_template('edit_post.html', post=post)




@app.route('/delete_post/<post_id>')
def delete_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    # Ensure only the post owner can delete the post
    if post['username'] != session['username']:
        flash('You do not have permission to delete this post.', 'error')
        return redirect(url_for('home'))
    
    # Delete the post
    posts.delete_one({'_id': post['_id']})
    
    # Add notification for post deletion
    add_notification(
        'post_deleted',
        f"🗑️ {post['username']}'s post '{post['title']}' was deleted",
        target_post=post
    )
    
    flash('Post deleted successfully!', 'success')
    return redirect(url_for('home'))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        # Check if the email already exists
        if users.find_one({'email': email}):
            flash('Email already exists', 'error')
            return render_template('signup.html')

        # Validate password strength
        if not is_strong_password(password):
            flash(
                'Password is too weak. It must be at least 8 characters long, '
                'contain at least one uppercase letter, one lowercase letter, '
                'one digit, and one special character (!@#$%^&*()).',
                'error'
            )
            return render_template('signup.html')

        # Validate password confirmation
        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return render_template('signup.html')

        # Create the user account
        users.insert_one({
            'username': username,
            'email': email,
            'password': password,  # In production, hash the password before storing
            'role': 'user'
        })
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Fetch approved posts and calculate contribution
    all_posts = posts.find({'status': 'approved'})
    posts_with_contribution = []
    for post in all_posts:
        contribution = post.get('upvotes', 0) - post.get('downvotes', 0)
        post['contribution'] = contribution
        posts_with_contribution.append(post)
    
    # Sort posts by contribution (descending order)
    posts_with_contribution.sort(key=lambda x: x['contribution'], reverse=True)
    
    return render_template('home.html', posts=posts_with_contribution)



@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        attachments = request.files.getlist('attachments')
        
        # Save attachments if provided
        attachment_urls = []
        for attachment in attachments:
            if attachment and allowed_file(attachment.filename):
                filename = secure_filename(attachment.filename)
                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                attachment.save(attachment_path)
                attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
        
        # Calculate the user's total contribution
        user_posts = posts.find({'username': session['username']})
        total_contribution = sum(post.get('upvotes', 0) - post.get('downvotes', 0) for post in user_posts)
        
        # Set post status based on total contribution
        status = 'approved' if total_contribution >= 50 else 'pending'
        
        # Create the post
        post = {
            'title': title,
            'content': content,
            'username': session['username'],
            'upvotes': 0,
            'downvotes': 0,
            'upvoted_by': [],
            'downvoted_by': [],
            'status': status,
            'attachment_urls': attachment_urls,
            'timestamp': datetime.now()
        }
        inserted_post = posts.insert_one(post)
        
        # Add notification only for approved posts
        if status == 'approved':
            add_notification(
                'post_approved',
                f"📝 New post by {session['username']}: '{title}'",
                target_post=post
            )
        
        flash(f'Post created successfully! Status: {status}.', 'success')
        return redirect(url_for('home'))
    
    return render_template('create_post.html')



@app.route('/upvote/<post_id>')
def upvote(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))

    if session['username'] in post.get('upvoted_by', []):
        flash('You have already upvoted this post.', 'error')
    else:
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$inc': {'upvotes': 1},
                '$push': {'upvoted_by': session['username']}
            }
        )
        flash('Post upvoted!', 'success')

    return redirect(url_for('view_topic', post_id=post_id))


@app.route('/downvote/<post_id>')
def downvote(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))

    if session['username'] in post.get('downvoted_by', []):
        flash('You have already downvoted this post.', 'error')
    else:
        posts.update_one(
            {'_id': ObjectId(post_id)},
            {
                '$inc': {'downvotes': 1},
                '$push': {'downvoted_by': session['username']}
            }
        )
        flash('Post downvoted!', 'success')

    return redirect(url_for('view_topic', post_id=post_id))


@app.route('/add_comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    comment_text = request.form['comment']
    parent_comment_id = request.form.get('parent_comment_id')  # Optional: ID of the parent comment
    attachments = request.files.getlist('attachments')
    
    # Save attachments if provided
    attachment_urls = []
    for attachment in attachments:
        if attachment and allowed_file(attachment.filename):
            filename = secure_filename(attachment.filename)
            attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            attachment.save(attachment_path)
            attachment_urls.append(url_for('static', filename=f'uploads/{filename}'))
    
    # Create the comment
    comment = {
        'post_id': ObjectId(post_id),
        'username': session['username'],
        'comment': comment_text,
        'attachment_urls': attachment_urls,
        'parent_comment_id': ObjectId(parent_comment_id) if parent_comment_id else None,
        'timestamp': datetime.now()
    }
    comments.insert_one(comment)
    
    # Fetch the post title for the notification
    post = posts.find_one({'_id': ObjectId(post_id)})
    if post:
        add_notification(
            'comment',
            f"{session['username']} commented on the post: {post['title']}",
            target_post=post
        )
    
    flash('Comment added successfully!', 'success')
    return redirect(url_for('view_topic', post_id=post_id))


def fetch_comments(post_id, parent_comment_id=None):
    """
    Recursively fetch comments and replies for a post.
    """
    query = {'post_id': ObjectId(post_id), 'parent_comment_id': parent_comment_id}
    comments_list = list(comments.find(query).sort('timestamp', 1))

    for comment in comments_list:
        comment['replies'] = fetch_comments(post_id, comment['_id'])  # Fetch replies recursively
    return comments_list



@app.route('/view_topic/<post_id>')
def view_topic(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Fetch the post
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found', 'error')
        return redirect(url_for('home'))
    
    # Check if the post is accessible to the current user
    if post['status'] != 'approved' and session['username'] != post['username'] and not is_moderator() and not is_admin():
        flash('You do not have permission to view this post.', 'error')
        return redirect(url_for('home'))
    
    # Fetch the author's details
    author = users.find_one({'username': post['username']})
    
    # Fetch comments and replies
    post_comments = fetch_comments(post_id)
    
    # Calculate total contribution
    total_contribution = post.get('upvotes', 0) - post.get('downvotes', 0)
    
    return render_template(
        'view_topic.html',
        post=post,
        author=author,
        comments=post_comments,
        total_contribution=total_contribution
    )



@app.route('/profile/<username>')
def profile(username):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Fetch the user's profile
    user = users.find_one({'username': username})
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('home'))
    
    # Determine which posts to display based on the logged-in user's role
    if session['username'] == username or is_moderator() or is_admin():
        # Post owner, moderator, or admin can see all posts
        user_posts = list(posts.find({'username': username}))
    else:
        # Other users can only see approved posts
        user_posts = list(posts.find({'username': username, 'status': 'approved'}))
    
    # Calculate total contribution, upvotes, and downvotes
    total_contribution = 0
    total_upvotes = 0
    total_downvotes = 0
    for post in user_posts:
        total_contribution += post.get('upvotes', 0) - post.get('downvotes', 0)
        total_upvotes += post.get('upvotes', 0)
        total_downvotes += post.get('downvotes', 0)
    
    return render_template(
        'profile.html',
        user=user,
        posts=user_posts,
        total_contribution=total_contribution,
        total_upvotes=total_upvotes,
        total_downvotes=total_downvotes
    )


@app.route('/notifications')
def notifications_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Fetch all notifications sorted by timestamp (newest first)
    all_notifications = list(notifications.find().sort('timestamp', -1))
    
    return render_template('notification.html', notifications=all_notifications)



@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Fetch dashboard data (e.g., post statistics, user activity, etc.)
    post_stats = {
        'approved': posts.count_documents({'status': 'approved'}),
        'pending': posts.count_documents({'status': 'pending'}),
        'rejected': posts.count_documents({'status': 'rejected'}),
        'total': posts.count_documents({})
    }

    user_activity = {
        'comments': comments.count_documents({}),
        'upvotes': sum(post.get('upvotes', 0) for post in posts.find()),
        'downvotes': sum(post.get('downvotes', 0) for post in posts.find())
    }

    approved_topics = list(posts.find({'status': 'approved'}, {'title': 1})) or []
    all_users = list(users.find({}, {'username': 1, 'role': 1})) or []
    pending_posts = list(posts.find({'status': 'pending'})) if is_moderator() else []

    return render_template(
        'dashboard.html',
        post_stats=post_stats,
        user_activity=user_activity,
        approved_topics=approved_topics,
        all_users=all_users,
        pending_posts=pending_posts
    )



# @app.route('/assign_moderator/<username>', methods=['POST'])
# def assign_moderator(username):
#     if not is_admin():
#         flash('Permission denied.', 'error')
#         return redirect(url_for('dashboard'))
    
#     user = users.find_one({'username': username})
#     if not user:
#         flash('User not found.', 'error')
#         return redirect(url_for('dashboard'))
    
#     # Update the user's role to moderator
#     users.update_one({'username': username}, {'$set': {'role': 'moderator'}})
    
#     # Add notification with a link to the user's profile
#     add_notification(
#         'moderator_assigned',
#         f"🚀 {session['username']} assigned {username} as moderator",
#         target_user=user
#     )
    
#     flash(f'{username} has been assigned as a moderator.', 'success')
#     return redirect(url_for('dashboard'))


@app.route('/dashboard/assign_moderator', methods=['POST'])
def assign_moderator_dashboard():
    if not is_admin():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    username = request.form.get('username')
    if not username:
        flash('No username selected.', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch the user being assigned as a moderator
    user = users.find_one({'username': username})
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Update the user's role to moderator
    users.update_one({'username': username}, {'$set': {'role': 'moderator'}})
    
    # Generate the notification message and link
    notification_message = f"🚀 {session['username']} assigned {username} as moderator"
    notification_link = url_for('profile', username=username)  # Link to the moderator's profile
    
    # Insert the notification into the collection
    notification = {
        'type': 'moderator_assigned',
        'message': notification_message,
        'link': notification_link,  # Store the clickable link
        'timestamp': datetime.now()
    }
    notifications.insert_one(notification)
    
    flash(f'{username} has been assigned as a moderator.', 'success')
    return redirect(url_for('dashboard'))


# Route for dashboard form submission (renamed)
@app.route('/dashboard/assign_moderator', methods=['POST'])
def dashboard_assign_moderator():
    if not is_admin():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    # Get the username from the form
    username = request.form.get('username')
    if not username:
        flash('No username selected.', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch the user details
    user = users.find_one({'username': username})
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Update the user's role to moderator
    users.update_one({'username': username}, {'$set': {'role': 'moderator'}})
    
    # Generate a clickable link to the moderator's profile
    profile_link = url_for('profile', username=user['username'], _external=True)
    
    # Add a notification for the moderator assignment
    add_notification(
        'moderator_assigned',
        f"🚀 {session['username']} assigned <a href='{profile_link}'>{username}</a> as moderator",
        target_user=user  # Pass the user object to generate the link
    )
    
    flash(f'{username} assigned as moderator.', 'success')
    return redirect(url_for('dashboard'))



@app.route('/dashboard/approve_reject')
def dashboard_approve_reject():
    if not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    # Fetch pending posts along with their author details
    pending_posts = list(posts.find({'status': 'pending'}))
    for post in pending_posts:
        post['author'] = users.find_one({'username': post['username']})  # Fetch author details
    
    return render_template('dashboard_approve_reject.html', posts=pending_posts)


@app.route('/dashboard/topics')
def dashboard_topics():
    if not is_admin() and not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    # Fetch approved posts
    approved_posts = posts.find({'status': 'approved'})
    return render_template('dashboard_topics.html', posts=approved_posts)

@app.route('/dashboard/profiles')
def dashboard_profiles():
    if not is_admin() and not is_moderator():
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('home'))
    
    # Fetch all users
    all_users = users.find()
    return render_template('dashboard_profiles.html', users=all_users)




@app.route('/approve_post/<post_id>')
def approve_post(post_id):
    post = posts.find_one({'_id': ObjectId(post_id)})
    posts.update_one({'_id': post['_id']}, {'$set': {'status': 'approved'}})
    add_notification(
        'post_approved',
        f"✅ {post['username']}'s post '{post['title']}' was approved",
        target_post=post
    )
    return redirect(url_for('dashboard'))



# Single Post Rejection
@app.route('/reject_post/<post_id>')
def reject_post(post_id):
    if not is_moderator():
        flash('Permission denied.', 'error')
        return redirect(url_for('home'))

    # Update post status to 'rejected' instead of deleting
    posts.update_one(
        {'_id': ObjectId(post_id)},
        {'$set': {'status': 'rejected'}}
    )

    # Add notification
    post = posts.find_one({'_id': ObjectId(post_id)})
    add_notification(f"{session['username']} rejected the post: {post['title']}")

    flash('Post rejected successfully!', 'success')
    return redirect(url_for('dashboard_topics'))


# Bulk Rejection
@app.route('/bulk_actions', methods=['POST'])
def bulk_actions():
    if not is_moderator():
        flash('Permission denied.', 'error')
        return redirect(url_for('dashboard'))
    
    # Get the list of post IDs and the action (approve/reject)
    post_ids = request.form.getlist('post_ids')
    action = request.form.get('action')
    
    if action == 'approve':
        for pid in post_ids:
            # Fetch the post details before updating its status
            post = posts.find_one({'_id': ObjectId(pid)})
            if post:
                # Update the post status to 'approved'
                posts.update_one(
                    {'_id': ObjectId(pid)},
                    {'$set': {'status': 'approved'}}
                )
                
                # Add notification for the approved post
                add_notification(
                    'post_approved',
                    f"✅ {post['username']}'s post '{post['title']}' was approved",
                    target_post=post
                )
        
        flash(f'Approved {len(post_ids)} posts.', 'success')
    
    elif action == 'reject':
        for pid in post_ids:
            # Fetch the post details before updating its status
            post = posts.find_one({'_id': ObjectId(pid)})
            if post:
                # Update the post status to 'rejected'
                posts.update_one(
                    {'_id': ObjectId(pid)},
                    {'$set': {'status': 'rejected'}}
                )
                
                # Add notification for the rejected post
                # add_notification(
                #     'post_rejected',
                #     f"❌ {post['username']}'s post '{post['title']}' was rejected",
                #     target_post=post
                # )
        
        flash(f'Rejected {len(post_ids)} posts.', 'success')
    
    return redirect(url_for('dashboard'))



@app.route('/post/<post_id>')
def view_post(post_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Fetch the post
    post = posts.find_one({'_id': ObjectId(post_id)})
    if not post:
        flash('Post not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Ensure only moderators or the post owner can view pending posts
    if post['status'] != 'approved' and not is_moderator() and post['username'] != session['username']:
        flash('You do not have permission to view this post.', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch comments for the post
    post_comments = list(comments.find({'post_id': ObjectId(post_id)}))
    
    return render_template('view_post.html', post=post, comments=post_comments)


@app.route('/search', methods=['POST'])
def search():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    query = request.form['query']
    search_type = request.form['search_type']
    
    if search_type == 'topic':
        # Only show approved posts to non-moderators/non-admins
        if is_moderator() or is_admin():
            results = posts.find({'title': {'$regex': query, '$options': 'i'}})
        else:
            results = posts.find({'title': {'$regex': query, '$options': 'i'}, 'status': 'approved'})
        return render_template('search_results.html', results=results, search_type='topic')
    
    elif search_type == 'email':
        user = users.find_one({'email': query})
        if user:
            # Only show approved posts to non-moderators/non-admins
            if is_moderator() or is_admin():
                results = posts.find({'username': user['username']})
            else:
                results = posts.find({'username': user['username'], 'status': 'approved'})
            return render_template('search_results.html', results=results, search_type='email', user=user)
        else:
            flash('User not found', 'error')
            return redirect(url_for('home'))
    
    return redirect(url_for('home'))


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# Register the custom filter
app.jinja_env.filters['make_links_clickable'] = make_links_clickable

if __name__ == '__main__':
    app.run(debug=True)