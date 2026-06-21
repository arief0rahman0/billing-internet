import os
import re

html_dir = 'templates'
for filename in os.listdir(html_dir):
    if filename.endswith('.html'):
        filepath = os.path.join(html_dir, filename)
        with open(filepath, 'r') as f:
            content = f.read()

        # Add csrf token to all forms that are method="POST"
        # We find <form ...> and append the token after it
        def form_replacer(match):
            form_tag = match.group(0)
            # If it's explicitly GET, don't add CSRF
            if 'method="GET"' in form_tag or "method='GET'" in form_tag:
                return form_tag
            # Add csrf_token
            return form_tag + '\n<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>'

        # This regex matches <form ...> tags
        new_content = re.sub(r'<form\b[^>]*>', form_replacer, content)

        # For index.html and edit_pelanggan.html, add JS
        if filename in ['index.html', 'edit_pelanggan.html']:
            js = """
<script>
document.addEventListener('click', function(e) {
    const a = e.target.closest('a');
    if (!a) return;
    
    const csrfToken = "{{ csrf_token() }}";
    const postUrls = ['/lunas/', '/kirim_wa_lunas/', '/kirim_wa_pengingat/', '/hapus_pelanggan/'];
    const href = a.getAttribute('href');
    
    if (href && postUrls.some(url => href.startsWith(url))) {
        e.preventDefault();
        if(confirm('Tindakan ini akan mengubah data. Lanjutkan?')) {
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = href;
            
            const csrfInput = document.createElement('input');
            csrfInput.type = 'hidden';
            csrfInput.name = 'csrf_token';
            csrfInput.value = csrfToken;
            
            form.appendChild(csrfInput);
            document.body.appendChild(form);
            form.submit();
        }
    }
});
</script>
</body>
"""
            new_content = new_content.replace('</body>', js)

        if new_content != content:
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Updated {filename}")

